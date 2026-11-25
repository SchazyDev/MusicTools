//! Единый реестр заданий (jobs).
//!
//! Прямой перенос `core/tools/base.py` (ToolWorker) на Rust: у каждого
//! длительного действия есть идентификатор, канал событий и токен отмены.
//! Команды Tauri только регистрируют задание и сразу возвращают `job_id`;
//! сами события уходят во фронтенд одним каналом `job://event`.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::sync::broadcast;

/// Имя события, в котором фронтенд получает **все** обновления заданий.
pub const EVENT_JOB: &str = "job://event";

/// Доля завершённости, которую job отдаёт в событие `Progress`.
pub type Percent = u8;

/// Финальный результат задания.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobOutcome {
    /// Успех; `message` — краткий статус пользователю на русском.
    Ok { message: String },
    /// Отменено пользователем.
    Canceled,
    /// Ошибка; `message` уже локализована под RU.
    Failed { message: String },
}

impl JobOutcome {
    pub fn ok(message: impl Into<String>) -> Self {
        Self::Ok { message: message.into() }
    }

    pub fn failed(message: impl Into<String>) -> Self {
        Self::Failed { message: message.into() }
    }
}

/// Ошибка выполнения задания.
///
/// `Cancelled` отделён от `Failed`, чтобы вызывающий код мог отличить отмену
/// от аварии. Зеркало `utils.constants.CancelException` из PyQt-версии.
#[derive(Debug, thiserror::Error)]
pub enum JobError {
    #[error("задание отменено")]
    Cancelled,
    #[error("{0}")]
    Failed(String),
}

impl From<std::io::Error> for JobError {
    fn from(e: std::io::Error) -> Self {
        JobError::Failed(format!("ошибка ввода-вывода: {e}"))
    }
}

pub type JobResult<T = ()> = Result<T, JobError>;

/// Состояние отмены. Клонируется в каждый job; `cancel()` идемпотентен
/// и безопасен для вызова из любого потока.
#[derive(Debug, Clone, Default)]
pub struct CancelToken(Arc<AtomicBool>);

impl CancelToken {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::SeqCst);
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
}

/// Событие, которое job отдаёт наружу.
///
/// Сериализуется с полем `state`: `progress`, `status`, `ok`, `canceled`,
/// `failed`. Поле `file` присутствует только у многофайловых операций.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum JobEvent {
    Progress {
        percent: Percent,
        #[serde(skip_serializing_if = "Option::is_none")]
        file: Option<String>,
    },
    Status {
        message: String,
    },
    #[serde(rename = "ok")]
    Ok {
        message: String,
    },
    Canceled,
    Failed {
        message: String,
    },
}

impl JobEvent {
    fn terminal(outcome: JobOutcome) -> Self {
        match outcome {
            JobOutcome::Ok { message } => Self::Ok { message },
            JobOutcome::Canceled => Self::Canceled,
            JobOutcome::Failed { message } => Self::Failed { message },
        }
    }
}

/// Обёртка, которая уходит во фронтенд единым событием `job://event`.
#[derive(Debug, Clone, Serialize)]
pub struct JobEventEnvelope {
    pub job_id: u64,
    pub kind: String,
    #[serde(flatten)]
    pub event: JobEvent,
}

/// Контекст, передаваемый в тело задания.
#[derive(Clone)]
pub struct JobContext {
    job_id: u64,
    kind: String,
    tx: broadcast::Sender<JobEventEnvelope>,
    cancel: CancelToken,
}

impl JobContext {
    pub fn job_id(&self) -> u64 {
        self.job_id
    }

    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Публикует событие. Отсутствие подписчиков — штатная ситуация (UI закрыт).
    fn emit(&self, event: JobEvent) {
        let _ = self.tx.send(JobEventEnvelope {
            job_id: self.job_id,
            kind: self.kind.clone(),
            event,
        });
    }

    pub fn progress(&self, percent: Percent) {
        self.emit(JobEvent::Progress { percent: percent.min(100), file: None });
    }

    pub fn progress_file(&self, percent: Percent, file: impl Into<String>) {
        self.emit(JobEvent::Progress { percent: percent.min(100), file: Some(file.into()) });
    }

    pub fn status(&self, message: impl Into<String>) {
        self.emit(JobEvent::Status { message: message.into() });
    }

    /// Проверка отмены — вызывается между шагами длинной операции.
    pub fn check_cancel(&self) -> JobResult<()> {
        if self.cancel.is_cancelled() {
            Err(JobError::Cancelled)
        } else {
            Ok(())
        }
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancel.is_cancelled()
    }

    /// Задержка, прерываемая отменой (аналог `QThread.msleep` без блокировки).
    pub async fn sleep(&self, ms: u64) -> JobResult<()> {
        tokio::time::sleep(Duration::from_millis(ms)).await;
        self.check_cancel()
    }

    /// Превращает результат «работы с ошибками» в [`JobOutcome`].
    pub fn guarded<T: Into<String>>(
        &self,
        work: impl FnOnce(&JobContext) -> JobResult<T>,
    ) -> JobOutcome {
        match work(self) {
            Ok(message) => JobOutcome::ok(message),
            Err(JobError::Cancelled) => JobOutcome::Canceled,
            Err(JobError::Failed(message)) => JobOutcome::failed(message),
        }
    }

    #[cfg(test)]
    fn cancel_for_test(&self) {
        self.cancel.cancel();
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Running,
    Finished,
    Canceled,
    Failed,
}

impl JobStatus {
    fn from_outcome(outcome: &JobOutcome) -> Self {
        match outcome {
            JobOutcome::Ok { .. } => Self::Finished,
            JobOutcome::Canceled => Self::Canceled,
            JobOutcome::Failed { .. } => Self::Failed,
        }
    }
}

struct JobEntry {
    kind: String,
    cancel: CancelToken,
    status: JobStatus,
}

type SharedJobs = Arc<RwLock<HashMap<u64, JobEntry>>>;

/// Снимок состояния задания для `list_jobs` и UI.
#[derive(Debug, Clone, Serialize)]
pub struct JobSnapshot {
    pub id: u64,
    pub kind: String,
    pub status: JobStatus,
}

/// Реестр всех заданий процесса и единственный публикатор событий.
pub struct JobRegistry {
    next_id: AtomicU64,
    jobs: SharedJobs,
    tx: broadcast::Sender<JobEventEnvelope>,
}

impl JobRegistry {
    /// Создаёт реестр и подписывает `app` на события всех заданий.
    pub fn new(app: AppHandle) -> Self {
        let (tx, mut rx) = broadcast::channel::<JobEventEnvelope>(512);

        tauri::async_runtime::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(envelope) => {
                        let id = envelope.job_id;
                        if let Err(e) = app.emit(EVENT_JOB, envelope) {
                            log::warn!("не удалось отправить событие задания {id}: {e}");
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(skipped)) => {
                        log::warn!("потеряно событий заданий: {skipped}");
                    }
                    Err(broadcast::error::RecvError::Closed) => break,
                }
            }
        });

        Self {
            next_id: AtomicU64::new(0),
            jobs: Arc::new(RwLock::new(HashMap::new())),
            tx,
        }
    }

    /// Запускает job и возвращает `job_id` сразу — команда не ждёт работы.
    ///
    /// Тело получает [`JobContext`], работает в tokio-runtime и обязано
    /// регулярно вызывать `check_cancel()`. Итог публикуется в `job://event`.
    pub fn spawn<F>(&self, kind: impl Into<String>, body: F) -> u64
    where
        F: FnOnce(JobContext) -> std::pin::Pin<Box<dyn std::future::Future<Output = JobOutcome> + Send>>
            + Send
            + 'static,
    {
        let kind = kind.into();
        let id = self.next_id.fetch_add(1, Ordering::SeqCst) + 1;
        let ctx = JobContext { job_id: id, kind: kind.clone(), tx: self.tx.clone(), cancel: CancelToken::default() };

        if let Ok(mut guard) = self.jobs.write() {
            guard.insert(
                id,
                JobEntry { kind: kind.clone(), cancel: ctx.cancel.clone(), status: JobStatus::Running },
            );
        }

        let shared = Arc::clone(&self.jobs);
        let tx = self.tx.clone();

        tauri::async_runtime::spawn(async move {
            let outcome = body(ctx).await;
            let status = JobStatus::from_outcome(&outcome);
            if let Ok(mut guard) = shared.write() {
                if let Some(entry) = guard.get_mut(&id) {
                    entry.status = status;
                }
            }
            let _ = tx.send(JobEventEnvelope { job_id: id, kind, event: JobEvent::terminal(outcome) });
        });

        id
    }

    pub fn cancel(&self, id: u64) -> bool {
        let token = self.jobs.read().ok().and_then(|guard| guard.get(&id).map(|e| e.cancel.clone()));
        match token {
            Some(token) => {
                token.cancel();
                true
            }
            None => false,
        }
    }

    pub fn snapshot(&self) -> Vec<JobSnapshot> {
        let Ok(guard) = self.jobs.read() else { return Vec::new() };
        let mut out: Vec<JobSnapshot> =
            guard.iter().map(|(id, e)| JobSnapshot { id: *id, kind: e.kind.clone(), status: e.status }).collect();
        out.sort_by_key(|s| s.id);
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_context() -> (JobContext, broadcast::Receiver<JobEventEnvelope>) {
        let (tx, rx) = broadcast::channel(16);
        (
            JobContext { job_id: 7, kind: "demo".into(), tx, cancel: CancelToken::default() },
            rx,
        )
    }

    #[test]
    fn events_are_tagged_and_serialized() {
        let (ctx, mut rx) = make_context();
        ctx.progress(42);
        ctx.status("шаг");
        let first = rx.try_recv().expect("событие прогресса");
        assert_eq!(first.job_id, 7);
        let json = serde_json::to_value(&first).expect("сериализация");
        assert_eq!(json["state"], "progress");
        assert_eq!(json["percent"], 42);
        assert!(json.get("file").is_none());
    }

    #[test]
    fn cancel_token_is_idempotent_and_guarded() {
        let (ctx, _rx) = make_context();
        assert!(ctx.check_cancel().is_ok());
        ctx.cancel_for_test();
        assert!(matches!(ctx.check_cancel(), Err(JobError::Cancelled)));
        let outcome = ctx.guarded(|c| c.check_cancel().map(|_| "ok"));
        assert!(matches!(outcome, JobOutcome::Canceled));
    }
}
