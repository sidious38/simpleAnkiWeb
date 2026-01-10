import os
import re
import hmac
import json
import urllib.request
import urllib.error
from datetime import datetime
from flask import (
    Flask, jsonify, request, send_from_directory,
    session, redirect, url_for, abort
)
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix

# =====================
# Environment variables
# =====================
REQUIRED_ENV_VARS = [
    "ANKI_CONNECT_URL",
    "FLASK_SECRET_KEY",
    "APP_USERNAME",
    "APP_PASSWORD",
]

missing = [v for v in REQUIRED_ENV_VARS if v not in os.environ]
if missing:
    raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

ANKI_CONNECT_URL = os.environ["ANKI_CONNECT_URL"]
FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]
APP_USERNAME = os.environ["APP_USERNAME"]
APP_PASSWORD = os.environ["APP_PASSWORD"]

# =====================
# Flask app
# =====================
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# Secure cookie settings
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# =====================
# AnkiConnect helpers
# =====================
def request_anki(action, **params):
    return {"action": action, "params": params, "version": 6}


def invoke(action, **params):
    request_json = json.dumps(
        request_anki(action, **params)
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            ANKI_CONNECT_URL,
            request_json,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=5) as resp:
            response = json.load(resp)

    except urllib.error.URLError as e:
        raise RuntimeError(f"AnkiConnect unreachable: {e}")

    if "error" not in response or "result" not in response:
        raise RuntimeError("Invalid response from AnkiConnect")

    if response["error"]:
        raise RuntimeError(response["error"])

    return response["result"]

# =====================
# Utils
# =====================
def replace_img_with_base64(answer):
    img_tag_pattern = r'<img[^>]+src="([^"]+)"[^>]*>'

    def img_to_base64(match):
        filename = match.group(1)
        data = invoke("retrieveMediaFile", filename=filename)
        return f'<img src="data:image/jpeg;base64,{data}" />'

    return re.sub(img_tag_pattern, img_to_base64, answer)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
           return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# =====================
# Error handlers
# =====================
@app.errorhandler(401)
def unauthorized(_):
    return "Unauthorized", 401


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": str(e)}), 500

# =====================
# Routes
# =====================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_ok = hmac.compare_digest(
            request.form.get("username", ""),
            APP_USERNAME
        )
        pass_ok = hmac.compare_digest(
            request.form.get("password", ""),
            APP_PASSWORD
        )

        if user_ok and pass_ok:
            session.clear()
            session["logged_in"] = True
            return redirect(url_for("index"))

        abort(401)

    return """
        <form method="post">
            <p><input name="username"></p>
            <p><input type="password" name="password"></p>
            <p><input type="submit" value="Login"></p>
        </form>
    """


@app.route("/")
@login_required
def index():
    return redirect(url_for("send_decks"))


@app.route("/getDeckNames")
@login_required
def get_deck_names():
    return jsonify(invoke("deckNames"))


def custom_sort(card):
    reps = card.get("reps", 0)
    if reps >= 1 and card["due"] < datetime.now().timestamp():
        return 0, card["due"]
    return 1, card["due"]


@app.route("/getCards")
@login_required
def get_cards():
    query = request.args.get("query", "")
    rs = invoke("findCards", query=f"deck:{query} and (is:new or is:due)")
    cards = invoke("cardsInfo", cards=rs)
    cards.sort(key=custom_sort)
    return jsonify([c["cardId"] for c in cards])


@app.route("/getNextCard")
@login_required
def get_next_card():
    query = request.args.get("query", "")
    rs = invoke("findCards", query=f"deck:{query} and (is:new or is:due)")
    cards = invoke("cardsInfo", cards=rs)
    cards.sort(key=custom_sort)
    return jsonify(cards[0]["cardId"])


@app.route("/getCardContent")
@login_required
def get_card_content():
    card = json.loads(request.args["card"])
    rs = invoke("cardsInfo", cards=[card])
    rs[0]["question"] = replace_img_with_base64(rs[0]["question"])
    rs[0]["answer"] = replace_img_with_base64(rs[0]["answer"])
    return jsonify(rs)


@app.route("/answerCard")
@login_required
def answer_card():
    card = json.loads(request.args["card"])
    ease = json.loads(request.args["ease"])
    return jsonify(
        invoke("answerCards", answers=[{"cardId": card, "ease": ease}])
    )


@app.route("/decks")
@login_required
def send_decks():
    invoke("sync")
    return send_from_directory("static", "selectDeck.html")


@app.route("/revise")
@login_required
def send_card():
    return send_from_directory("static", "showCard.html")
