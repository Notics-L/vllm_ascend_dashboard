"""Encrypted GitHub-to-WeLink recipient mappings owned by the Daily module."""

from sqlalchemy import Column, Integer, String, Text

from . import Base


class DailyPipelineNotificationRecipient(Base):
    __tablename__ = "daily_pipeline_notification_recipients"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    github_name = Column(String(100), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    account = Column(Text, nullable=False)  # Encrypted WeLink employee account.
