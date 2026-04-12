import os


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SESSION_PERMANENT = False

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.environ.get(
        "GOOGLE_REDIRECT_URI", "http://localhost:5001/auth/google/callback"
    )

    MICROSOFT_CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "")
    MICROSOFT_CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
    MICROSOFT_REDIRECT_URI = os.environ.get(
        "MICROSOFT_REDIRECT_URI", "http://localhost:5001/auth/microsoft/callback"
    )

    PHASH_THRESHOLD = int(os.environ.get("PHASH_THRESHOLD", "10"))
    DEBUG_MODE = os.environ.get("DEBUG_MODE", "false").lower() == "true"
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max form submission


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SESSION_TYPE = "filesystem"
    SESSION_FILE_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "flask_session"
    )


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_TYPE = "filesystem"
    SESSION_FILE_DIR = "/tmp/flask_session"
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = "https"


class TestingConfig(BaseConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-secret-key"
    SESSION_TYPE = "filesystem"
    SESSION_FILE_DIR = "/tmp/flask_session_test"
