"""Configuration owned by the image build module."""

from sqlalchemy import Column, DateTime, String, Text, text

from infrastructure.persistence.models import Base


class ImageBuildConfig(Base):
    __tablename__ = "image_build_config"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    config_key = Column(String(64), primary_key=True)
    config_value = Column(Text, nullable=False)
    update_at = Column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )
