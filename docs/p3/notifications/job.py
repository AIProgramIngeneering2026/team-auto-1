"""Job — одно уведомление со своим жизненным циклом.

Состояния и переходы взяты один в один из диаграммы состояний практики 2
(architecture.md, раздел 3):

    принято (accepted) -> отправляется (sending)   Dispatcher запустил отправку
    отправляется -> отправляется                   повторная попытка, attempts + 1
    отправляется -> доставлено (delivered)         канал подтвердил приём
    отправляется -> не доставлено (undelivered)    попытки исчерпаны / постоянная
                                                   ошибка / перезапуск сервиса
    принято -> отменено (cancelled)                 отмена клиентом

Всё остальное запрещено. Правило живёт ЗДЕСЬ, в классе Job, а не в
вызывающем коде: состояние Job можно изменить только методами-событиями
(start_sending / record_retry / mark_delivered / mark_undelivered /
interrupt / cancel), каждый из которых проверяет переход по таблице
``_TRANSITIONS`` и при недопустимости бросает InvalidTransitionError —
недопустимый переход отклоняется явной ошибкой, а не игнорируется молча.

Из вне Job выглядит неизменяемым: ``state`` — свойство только для чтения,
атрибуты ``id``/``channel``/``recipient``/``text`` после создания не
меняются (Job неизменяем после создания — см. таблицу запрещённых
переходов в architecture.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from notifications.errors import InvalidTransitionError


class JobState(str, Enum):
    """Пять состояний Job из требований (requirements.md, «Жизненный цикл Job»)."""

    ACCEPTED = "accepted"          # принято: создан, отправка ещё не начиналась
    SENDING = "sending"            # отправляется: исполнитель обращается к каналу
    DELIVERED = "delivered"        # доставлено: терминальное
    UNDELIVERED = "undelivered"    # не доставлено: терминальное
    CANCELLED = "cancelled"        # отменено: терминальное


#: Терминальные состояния: переходы из них запрещены, Job «не оживает».
TERMINAL_STATES = frozenset(
    {JobState.DELIVERED, JobState.UNDELIVERED, JobState.CANCELLED}
)


@dataclass(frozen=True)
class _TransitionRule:
    """Один шаг диаграммы состояний: событие -> целевое состояние.

    ``sources`` — состояния, из которых событие допустимо.
    Пара (событие, исходное состояние), отсутствующая в правиле, запрещена.
    """

    event: str
    target: JobState
    sources: frozenset[JobState]


#: Таблица разрешённых переходов — «источник истины» для машины состояний.
#: Ключ — имя события; запись читается так: «событие start переводит Job
#: в sending, и допустимо это только из accepted».
#: Остальные пары (событие, состояние) запрещены и дают InvalidTransitionError.
_TRANSITIONS: dict[str, _TransitionRule] = {
    # Dispatcher взял Job в работу (accepted -> sending).
    "start": _TransitionRule(
        event="start",
        target=JobState.SENDING,
        sources=frozenset({JobState.ACCEPTED}),
    ),
    # Повторная попытка канала (sending -> sending), attempts + 1.
    "retry": _TransitionRule(
        event="retry",
        target=JobState.SENDING,
        sources=frozenset({JobState.SENDING}),
    ),
    # Канал подтвердил приём (sending -> delivered).
    "deliver": _TransitionRule(
        event="deliver",
        target=JobState.DELIVERED,
        sources=frozenset({JobState.SENDING}),
    ),
    # Попытки исчерпаны или постоянная ошибка (sending -> undelivered).
    "fail": _TransitionRule(
        event="fail",
        target=JobState.UNDELIVERED,
        sources=frozenset({JobState.SENDING}),
    ),
    # Перезапуск сервиса (sending -> undelivered, error = interrupted).
    "interrupt": _TransitionRule(
        event="interrupt",
        target=JobState.UNDELIVERED,
        sources=frozenset({JobState.SENDING}),
    ),
    # Отмена клиентом до начала отправки (accepted -> cancelled).
    "cancel": _TransitionRule(
        event="cancel",
        target=JobState.CANCELLED,
        sources=frozenset({JobState.ACCEPTED}),
    ),
}


class Job:
    """Одно уведомление: содержимое + состояние жизненного цикла.

    Хранение — память (выбрано для практики); сериализация в БД будет
    следующим шагом и контракт класса не изменит.
    """

    __slots__ = (
        "_id",
        "_channel",
        "_recipient",
        "_text",
        "_state",
        "_attempts",
        "_error",
        "_provider_message_id",
    )

    def __init__(
        self,
        job_id: str,
        channel: str,
        recipient: str,
        text: str,
    ) -> None:
        """Новый Job: создаётся ТОЛЬКО в состоянии accepted.

        Подсунуть состояние через конструктор нельзя — иначе машина
        состояний обходилась бы без проверки переходов. Восстановить
        сохранённый Job из хранилища можно только через ``Job.rehydrate()``
        (ниже), а перевести в другое состояние — только методами-событиями.
        """
        if not job_id or not channel or not recipient or not text:
            raise ValueError(
                "job_id, channel, recipient и text обязательны и не могут быть пустыми"
            )
        self._id = job_id
        self._channel = channel
        self._recipient = recipient
        self._text = text
        self._state = JobState.ACCEPTED
        self._attempts = 0
        self._error: str | None = None
        self._provider_message_id: str | None = None

    @classmethod
    def rehydrate(
        cls,
        job_id: str,
        channel: str,
        recipient: str,
        text: str,
        state: JobState,
        attempts: int = 0,
        error: str | None = None,
        provider_message_id: str | None = None,
    ) -> "Job":
        """Восстановить Job из хранилища (snapshot, не переход).

        Единственная точка, где состояние задаётся напрямую — и она
        предназначена для чтения сохранённого Job (персистентность,
        GET /notifications/{id}), а не для смены состояний. Восстановленный
        объект дальше живёт по тем же правилам: меняется только событиями,
        запрещённые переходы дают InvalidTransitionError.
        """
        if not job_id or not channel or not recipient or not text:
            raise ValueError(
                "job_id, channel, recipient и text обязательны и не могут быть пустыми"
            )
        if not isinstance(state, JobState):
            raise ValueError(
                f"state должно быть JobState, получено {state!r}"
            )
        if attempts < 0:
            raise ValueError(f"attempts не может быть отрицательным: {attempts}")
        if state is JobState.UNDELIVERED and not error:
            raise ValueError(
                "восстановление undelivered без причины ошибки запрещено "
                "(клиент должен видеть причину в error)"
            )
        job = cls(job_id, channel, recipient, text)
        job._state = state
        job._attempts = attempts
        job._error = error
        job._provider_message_id = provider_message_id
        return job

    # ------------------------------------------------------------------
    # Только чтение: содержимое Job неизменяемо после создания,
    # состояние меняют только методы-события ниже.
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return self._id

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def recipient(self) -> str:
        return self._recipient

    @property
    def text(self) -> str:
        return self._text

    @property
    def state(self) -> JobState:
        return self._state

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def error(self) -> str | None:
        """Причина неудачи; заполняется при переходе в undelivered."""
        return self._error

    @property
    def provider_message_id(self) -> str | None:
        return self._provider_message_id

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    # ------------------------------------------------------------------
    # Механика машины состояний. Вызывающий код её не видит и не может
    # обойти: прямого сеттера state нет, ``_apply`` — приватный.
    # ------------------------------------------------------------------

    def _apply(self, event: str) -> None:
        """Применить событие к текущему состоянию.

        Ищет правило события; если текущее состояние не входит в
        допустимые источники — InvalidTransitionError, состояние
        (и attempts, и error) не меняются.
        """
        rule = _TRANSITIONS[event]
        if self._state not in rule.sources:
            raise InvalidTransitionError(
                from_state=self._state.value,
                to_state=rule.target.value,
                event=event,
            )
        self._state = rule.target

    # ------------------------------------------------------------------
    # События жизненного цикла. Каждый метод — одна стрелка диаграммы.
    # ------------------------------------------------------------------

    def start_sending(self) -> None:
        """Dispatcher взял Job в работу: accepted -> sending, attempts = 1.

        При запуске отправки начинается первая попытка — счётчик attempts
        сразу начинает расти (architecture.md, таблица переходов).
        """
        self._apply("start")
        self._attempts += 1

    def record_retry(self) -> None:
        """Временная ошибка канала: sending -> sending, attempts + 1.

        Стратегию повторов (сколько попыток всего, паузы между ними) решает
        Dispatcher — здесь фиксируется сам факт повторной попытки.
        """
        self._apply("retry")
        self._attempts += 1

    def mark_delivered(self, provider_message_id: str | None = None) -> None:
        """Канал подтвердил приём: sending -> delivered. Терминальное."""
        self._apply("deliver")
        self._provider_message_id = provider_message_id

    def mark_undelivered(self, error: str) -> None:
        """Попытки исчерпаны или постоянная ошибка: sending -> undelivered.

        Причина обязательна: пустой error оставил бы клиента без ответа
        «почему не доставлено» (сценарий С2/S5), поэтому молча принять
        пустую причину нельзя.
        """
        if not error:
            raise ValueError(
                "причина ошибки обязательна для перехода в undelivered"
            )
        self._apply("fail")
        self._error = error

    def interrupt(self) -> None:
        """Перезапуск сервиса: sending -> undelivered, error = interrupted."""
        self._apply("interrupt")
        self._error = "interrupted"

    def cancel(self) -> None:
        """Отмена клиентом: accepted -> cancelled. Терминальное."""
        self._apply("cancel")

    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        return (
            f"Job(id={self._id!r}, channel={self._channel!r}, "
            f"state={self._state.value!r}, attempts={self._attempts}, "
            f"error={self._error!r})"
        )
