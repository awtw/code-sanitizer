"""Settings. Owner: wang.xiaoming@corp.acme.com"""
import os

# NOTE(jsmith-acme): values below were copied from /Users/jdoe/projects/paygw/.env
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://paygw_app:Pr0d-Pa55w0rd!@db01.corp.acme.com:5432/paygw",
)
JWT_SECRET = os.getenv("JWT_SECRET", "9f8c2d7e6b5a4f3e2d1c0b9a8f7e6d5c")
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
SUPPORT_CONTACT = "Contact Jane Smith <jane.smith@corp.acme.com> for PAYGW access"
DOCS_URL = "https://wiki.corp.acme.com/display/PAYGW/Home#setup"  # internal wiki
ALLOWED_ORIGINS = ["https://portal.corp.acme.com", "http://localhost:4200"]


class Settings:
    service_name = "paygw-api"
    log_dir = r"C:\Users\jdoe\paygw\logs"


settings = Settings()
