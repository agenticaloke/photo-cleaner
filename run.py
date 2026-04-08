import os
if os.environ.get("FLASK_ENV") != "production":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # Allow HTTP for local dev only

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
