"""Durable management jobs and a refreshable index of backtest report files."""

from sqlalchemy import select

from .models import BacktestReport, ManagementJob


class ManagementRepository:
    def __init__(self, database):
        self.database = database

    def import_jobs(self, jobs):
        """Copy missing legacy records without replacing any existing database record."""
        with self.database.session(write=True, lock_key="management:jobs:import") as session:
            for job in jobs:
                if session.get(ManagementJob, job["id"]) is None:
                    session.add(
                        ManagementJob(
                            id=job["id"], created_at=job.get("created_at", ""), payload=job
                        )
                    )

    def list_jobs(self):
        with self.database.session() as session:
            return [dict(item) for item in session.scalars(select(ManagementJob.payload)).all()]

    def save_job(self, job):
        with self.database.session(write=True, lock_key="management:job:" + job["id"]) as session:
            item = session.get(ManagementJob, job["id"])
            if item is None:
                session.add(
                    ManagementJob(id=job["id"], created_at=job.get("created_at", ""), payload=job)
                )
            else:
                item.payload = dict(job)

    def refresh_reports(self, reports):
        # Retain prior entries when a data volume is unavailable. The UI still
        # displays only validated files that were found during its current scan.
        with self.database.session(write=True, lock_key="management:reports") as session:
            old = {item.url: item for item in session.scalars(select(BacktestReport)).all()}
            for report in reports:
                item = old.pop(report["url"], None)
                if item is None:
                    session.add(
                        BacktestReport(
                            url=report["url"], modified_at=report["modified_at"], payload=report
                        )
                    )
                else:
                    item.modified_at, item.payload = report["modified_at"], dict(report)
