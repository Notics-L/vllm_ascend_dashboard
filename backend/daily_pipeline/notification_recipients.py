"""Single-record management of the encrypted notification recipient directory."""

from infrastructure.core.security import encrypt_api_key
from sqlalchemy.exc import IntegrityError

from daily_pipeline.notifications import wake_notifications


def recipient_view(row):
    return {"id": row.id, "githubName": row.github_name, "name": row.name}


class DailyNotificationRecipientService:
    def __init__(self, repository):
        self.repository = repository

    async def list(self):
        return [recipient_view(row) for row in await self.repository.list()]

    async def save(self, github_name, name, account, identity=None):
        if identity is None:
            if not account:
                raise ValueError("请输入工号")
            row = self.repository.add(github_name, name, encrypt_api_key(account))
        else:
            row = await self.repository.get(identity)
            if row is None:
                raise LookupError("映射不存在")
            if github_name.lower() != row.github_name:
                raise ValueError("GitHub 用户名不能修改")
            row.name = name
            if account:
                row.account = encrypt_api_key(account)
        try:
            await self.repository.commit()
        except IntegrityError as exc:
            await self.repository.rollback()
            raise ValueError("该 GitHub 用户已有映射") from exc
        result = recipient_view(row)
        await wake_notifications(self.repository.db)
        return result

    async def delete(self, identity):
        row = await self.repository.get(identity)
        if row is None:
            raise LookupError("映射不存在")
        await self.repository.delete(row)
        await self.repository.commit()
        return {"deleted": 1}

    async def delete_many(self, identities):
        rows = []
        for identity in sorted(identities):
            row = await self.repository.get(identity)
            if row is None:
                raise LookupError("映射不存在，请刷新后重试")
            rows.append(row)
        for row in rows:
            await self.repository.delete(row)
        await self.repository.commit()
        return {"deleted": len(rows)}
