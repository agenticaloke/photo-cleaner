import os
import tempfile
import shutil
import threading
import time

from flask import Flask
from flask_session import Session
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv

load_dotenv()

csrf = CSRFProtect()


def create_app(config_name=None):
    app = Flask(__name__)

    if config_name == "testing":
        app.config.from_object("config.TestingConfig")
    elif os.environ.get("FLASK_ENV") == "production":
        app.config.from_object("config.ProductionConfig")
    else:
        app.config.from_object("config.DevelopmentConfig")

    # Use Flask-Session for local dev (filesystem), skip for production (use signed cookies)
    if app.config.get("SESSION_TYPE") and app.config["SESSION_TYPE"] != "null":
        Session(app)
    csrf.init_app(app)

    from app.auth.google_auth import google_auth_bp
    from app.auth.microsoft_auth import microsoft_auth_bp
    from app.web.routes import web_bp

    app.register_blueprint(google_auth_bp)
    app.register_blueprint(microsoft_auth_bp)
    app.register_blueprint(web_bp)

    _start_temp_cleanup(app)

    return app


def _start_temp_cleanup(app):
    """Periodically clean up old temp directories."""
    def cleanup_loop():
        while True:
            time.sleep(3600)
            temp_base = tempfile.gettempdir()
            try:
                for name in os.listdir(temp_base):
                    if name.startswith("photocleaner-"):
                        path = os.path.join(temp_base, name)
                        age = time.time() - os.path.getmtime(path)
                        if age > 3600:
                            shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass

    t = threading.Thread(target=cleanup_loop, daemon=True)
    t.start()
