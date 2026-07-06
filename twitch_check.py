#!/usr/bin/env python3
"""
Twitch Live Notifier - edición GitHub Actions
-----------------------------------------------
Versión pensada para ejecutarse UNA VEZ cada vez que GitHub Actions la
lanza (según el horario definido en el workflow), en vez de en bucle
infinito como la versión de escritorio. Notifica solo por Telegram (no hay
"escritorio" en un runner de GitHub).

Toda la configuración se lee de variables de entorno (Secrets de GitHub),
nunca de un config.json, porque aquí no hay una persona tecleando datos.

El estado (qué canales estaban en directo la última vez) se guarda en
state.json, en el propio repositorio; el workflow de GitHub Actions se
encarga de hacer commit de ese archivo tras cada ejecución para que el
siguiente chequeo recuerde el estado anterior.
"""

import json
import os
import sys
from pathlib import Path

import requests

STATE_PATH = Path(__file__).parent / "state.json"


def env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Falta la variable de entorno/secreto: {name}")
    return value


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(live_now):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(live_now), f)


def get_app_token(client_id, client_secret):
    resp = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def refresh_user_token(client_id, client_secret, refresh_token):
    resp = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_own_user_id(client_id, user_token):
    headers = {"Client-Id": client_id, "Authorization": f"Bearer {user_token}"}
    resp = requests.get("https://api.twitch.tv/helix/users", headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()["data"]
    if not data:
        sys.exit("No se pudo identificar el usuario con ese token.")
    return data[0]["id"]


def get_followed_channels(client_id, user_token, user_id):
    headers = {"Client-Id": client_id, "Authorization": f"Bearer {user_token}"}
    logins = []
    cursor = None
    while True:
        params = {"user_id": user_id, "first": 100}
        if cursor:
            params["after"] = cursor
        resp = requests.get(
            "https://api.twitch.tv/helix/channels/followed",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        logins.extend(item["broadcaster_login"].lower() for item in payload.get("data", []))
        cursor = payload.get("pagination", {}).get("cursor")
        if not cursor:
            break
    return logins


def get_live_channels(client_id, app_token, usernames):
    headers = {"Client-Id": client_id, "Authorization": f"Bearer {app_token}"}
    live = {}
    for i in range(0, len(usernames), 100):
        batch = usernames[i : i + 100]
        params = [("user_login", u) for u in batch]
        resp = requests.get(
            "https://api.twitch.tv/helix/streams",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        for stream in resp.json().get("data", []):
            live[stream["user_login"].lower()] = stream
    return live


def send_telegram_notification(bot_token, chat_id, channel, title):
    text = f"\U0001F534 {channel} está en directo\n{title or '(sin título)'}"
    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    if not resp.ok:
        print(f"Aviso: fallo enviando Telegram: {resp.text}")


def main():
    client_id = env("TWITCH_CLIENT_ID")
    client_secret = env("TWITCH_CLIENT_SECRET")
    refresh_token = env("TWITCH_REFRESH_TOKEN")
    telegram_bot_token = env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = env("TELEGRAM_CHAT_ID")

    app_token = get_app_token(client_id, client_secret)
    tokens = refresh_user_token(client_id, client_secret, refresh_token)
    user_token = tokens["access_token"]

    # Twitch puede (raramente) devolver un refresh_token distinto al usarlo.
    # Si pasa, el siguiente run fallaría con 401; avisamos por si acaso.
    new_refresh_token = tokens.get("refresh_token")
    if new_refresh_token and new_refresh_token != refresh_token:
        print(
            "::warning::Twitch devolvió un nuevo refresh_token. "
            "Actualiza el secreto TWITCH_REFRESH_TOKEN con este valor: "
            + new_refresh_token
        )

    user_id = get_own_user_id(client_id, user_token)
    followed = get_followed_channels(client_id, user_token, user_id)

    live_channels = get_live_channels(client_id, app_token, followed)
    currently_live = set(live_channels.keys())
    previously_live = load_state()

    newly_live = currently_live - previously_live
    for channel in newly_live:
        title = live_channels[channel].get("title")
        send_telegram_notification(telegram_bot_token, telegram_chat_id, channel, title)
        print(f"Aviso enviado: {channel} ha empezado a emitir.")

    if not newly_live:
        print("Sin novedades: nadie nuevo en directo.")

    save_state(currently_live)


if __name__ == "__main__":
    main()
