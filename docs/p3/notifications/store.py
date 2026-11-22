"""In-memory хранилище Job — интерфейс хранения для ядра.

Ядро (Job, Registry, Dispatcher, Tool) не зависит от БД: если ядру нужно
хранение — через интерфейс (требование практики). Здесь достаточно
реализации в памяти; PostgreSQL-реализация с тем же контрактом появится
позже и ядро не изменится.

Главная защита, которая живёт здесь — «воскрешение» Job (злодейства
З3/З4/З6 из атаки): ``restore()`` для уже известного id допускает только
идентичный снимок; попытка восстановить Job в ДРУГОМ состоянии — в том
числе перевести терминальный Job в delivered/sending — отклоняется явной
ошибкой InvalidTransitionError, как условный UPDATE в ADR-0001. Один id —
один живой объект Job (``get`` всегда возвращает его), поэтому «создать
параллельную копию Job с готовым состоянием» через хранилище нельзя.
"""

from __future__ import annotations

from notifications.errors import InvalidTransitionError, JobNotFoundError
from notifications.job import Job, JobState


class InMemoryJobStore:
    """Хранилище Job в памяти: один Job на id, условное восстановление."""

    __slots__ = ("_jobs",)

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    # ------------------------------------------------------------------
    # Новый Job (входной слой: POST /notifications)
    # ------------------------------------------------------------------

    def add(self, job: Job) -> None:
        """Сохранить НОВЫЙ Job. Новым может быть только accepted."""
        if not isinstance(job, Job):
            raise ValueError(f"ожидался Job, получено {type(job).__name__}")
        if job.id in self._jobs:
            raise ValueError(f"Job с id {job.id!r} уже существует")
        if job.state is not JobState.ACCEPTED:
            raise ValueError(
                "новый Job создаётся только в accepted "
                "(восстановление сохранённого — через restore)"
            )
        self._jobs[job.id] = job

    # ------------------------------------------------------------------
    # Чтение (Dispatcher, GET /notifications/{id})
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Job:
        """Единственный живой объект Job с этим id; иначе JobNotFoundError."""
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFoundError(job_id) from None

    def jobs_in_state(self, state: JobState) -> list[Job]:
        """Все Job в заданном состоянии (перезапуск: найти sending)."""
        if not isinstance(state, JobState):
            raise ValueError(f"state должно быть JobState, получено {state!r}")
        return [job for job in self._jobs.values() if job.state is state]

    # ------------------------------------------------------------------
    # Восстановление из персистентного снимка (перезапуск сервиса)
    # ------------------------------------------------------------------

    def restore(
        self,
        job_id: str,
        channel: str,
        recipient: str,
        text: str,
        state: JobState,
        attempts: int = 0,
        error: str | None = None,
        provider_message_id: str | None = None,
    ) -> Job:
        """Восстановить Job из сохранённого снимка — единственная точка
        входа для ``Job._rehydrate``.

        Правила (условная запись, как conditional UPDATE в ADR-0001):
        - id неизвестен — создаётся восстановленный Job (перезапуск,
          хранилище пустое, снимки приходят из БД);
        - id известен и снимок ИДЕНТИЧЕН текущему — возвращается тот же
          объект (повторное чтение того же снимка идемпотентно);
        - id известен и снимок ДРУГОЙ — InvalidTransitionError: это
          «воскрешение» — смена состояния Job без события (З3/З4/З6).
          Для терминальных Job это запрещено контрактом, для остальных —
          рассинхронизация хранилища, которую ядро тоже не пропускает.

        Инварианты самого снимка (успех ⇒ попытки были, error только у
        undelivered и только допустимых кодов, и т.д.) проверяет
        ``Job._rehydrate``.
        """
        existing = self._jobs.get(job_id)
        if existing is not None:
            same_snapshot = (
                existing.state is state
                and existing.attempts == attempts
                and existing.error == error
                and existing.provider_message_id == provider_message_id
            )
            if same_snapshot:
                return existing
            raise InvalidTransitionError(
                from_state=existing.state.value,
                to_state=getattr(state, "value", repr(state)),
                event="restore",
            )
        job = Job._rehydrate(
            job_id, channel, recipient, text,
            state, attempts, error, provider_message_id,
        )
        self._jobs[job_id] = job
        return job
