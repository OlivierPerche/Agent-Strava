"""
Strava + Google Calendar MCP Server
Expose des outils Strava et Google Calendar à Claude via MCP (Streamable HTTP).

Optimisation mémoire v2 :
- get_activity_streams : sous-échantillonnage à 1 point/5s (-80% RAM)
  Impact sur calculs : NP ±1.4W, pTSS ±3, hrTSS <0.1, Tps Z1-Z2 <0.5%
- Cache services Google Sheets/Calendar (déjà présent, conservé)
"""

from fastmcp import FastMCP
import requests
import os
import json
import time
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# --- Cache du token Strava en mémoire ---
_token_cache = {"access_token": None, "expires_at": 0}

def get_access_token() -> str:
    """
    Récupère un access_token Strava valide.
    Utilise le refresh_token pour en obtenir un nouveau si expiré.
    Met en cache pour éviter de spammer l'endpoint OAuth.
    """
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "refresh_token": os.getenv("STRAVA_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = data.get("expires_at", time.time() + 3600)
    return data["access_token"]


def _strava_get(path: str, params: dict | None = None) -> dict | list:
    """Helper pour appeler l'API Strava avec gestion du token."""
    token = get_access_token()
    response = requests.get(
        f"https://www.strava.com/api/v3{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _summarize_activity(activity: dict) -> dict:
    """Résumé court d'une activité (pour les listes)."""
    return {
        "id": activity.get("id"),
        "name": activity.get("name"),
        "type": activity.get("sport_type") or activity.get("type"),
        "start_date_local": activity.get("start_date_local"),
        "distance_km": round(activity.get("distance", 0) / 1000, 2),
        "moving_time_min": round(activity.get("moving_time", 0) / 60, 1),
        "elevation_gain_m": activity.get("total_elevation_gain", 0),
        "average_heartrate": activity.get("average_heartrate"),
        "max_heartrate": activity.get("max_heartrate"),
        "average_speed_kmh": round(activity.get("average_speed", 0) * 3.6, 2),
        "kudos_count": activity.get("kudos_count"),
    }


def _subsample_stream(data: list, step: int = 5) -> list:
    """
    Sous-échantillonne un stream en prenant 1 point tous les `step` points.
    Réduit la taille de ~80% pour step=5.
    Impact sur NP : ±1.4W (<1%), hrTSS : <0.1, Tps Z1-Z2 : <0.5%
    NOTE : lors des calculs côté Claude, adapter la fenêtre NP et multiplier
    dt par step. Ex : step=5 → fenêtre NP = 6 points (= 30s), dt = 5s.
    """
    return data[::step]


# --- Auth Google ---
_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]
_google_creds_cache: dict = {"credentials": None}

# Cache des services Google pour éviter le rechargement
# du schéma JSON à chaque appel (cause secondaire des crashes mémoire)
_sheets_service_cache = None
_calendar_service_cache = None


def get_google_credentials() -> Credentials:
    """
    Retourne des Credentials Google valides, avec cache et refresh automatique.
    """
    creds: Credentials | None = _google_creds_cache["credentials"]

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _google_creds_cache["credentials"] = creds
        return creds

    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_GOOGLE_SCOPES,
    )
    creds.refresh(Request())
    _google_creds_cache["credentials"] = creds
    return creds


def _get_calendar_service():
    """Retourne un service Google Calendar v3 authentifié — mis en cache."""
    global _calendar_service_cache
    if _calendar_service_cache is None:
        _calendar_service_cache = build(
            "calendar", "v3", credentials=get_google_credentials()
        )
    return _calendar_service_cache


def get_sheets_service():
    """Retourne un service Google Sheets v4 authentifié — mis en cache."""
    global _sheets_service_cache
    if _sheets_service_cache is None:
        _sheets_service_cache = build(
            "sheets", "v4", credentials=get_google_credentials()
        )
    return _sheets_service_cache


# --- Helpers Google Calendar ---
def _to_rfc3339(d: str) -> str:
    """Convertit 'YYYY-MM-DD' en RFC3339 requis par l'API Google Calendar."""
    if "T" in d:
        return d if d.endswith("Z") else d + "Z"
    return f"{d}T00:00:00Z"


def _summarize_event(event: dict) -> dict:
    """Résumé court d'un événement (pour les listes)."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id"),
        "title": event.get("summary"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "description": event.get("description"),
        "location": event.get("location"),
        "color_id": event.get("colorId"),
        "status": event.get("status"),
    }


# --- Serveur MCP ---
mcp = FastMCP("Strava Coach")


@mcp.tool
def get_recent_activities(per_page: int = 10) -> list[dict]:
    """
    Retourne les N dernières activités de l'athlète (résumé).

    Args:
        per_page: nombre d'activités à retourner (max 30 recommandé).
    """
    per_page = min(max(per_page, 1), 30)
    activities = _strava_get("/athlete/activities", {"per_page": per_page})
    return [_summarize_activity(a) for a in activities]


@mcp.tool
def get_activities_by_date_range(
    after: str,
    before: str | None = None,
    per_page: int = 30,
) -> list[dict]:
    """
    Retourne les activités dans une plage de dates.

    Args:
        after: date ISO 'YYYY-MM-DD' (inclusive).
        before: date ISO 'YYYY-MM-DD' (inclusive). Optionnel.
        per_page: max d'activités à retourner.
    """
    def _to_epoch(d: str) -> int:
        return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())

    params = {"per_page": min(per_page, 100), "after": _to_epoch(after)}
    if before:
        params["before"] = _to_epoch(before)

    activities = _strava_get("/athlete/activities", params)
    return [_summarize_activity(a) for a in activities]


@mcp.tool
def get_activity_details(activity_id: int) -> dict:
    """
    Retourne tous les détails d'une activité : splits, segments, FC, allure, etc.

    Args:
        activity_id: ID Strava de l'activité (obtenu via get_recent_activities).
    """
    activity = _strava_get(f"/activities/{activity_id}")
    return {
        "id": activity.get("id"),
        "name": activity.get("name"),
        "type": activity.get("sport_type") or activity.get("type"),
        "start_date_local": activity.get("start_date_local"),
        "description": activity.get("description"),
        "distance_km": round(activity.get("distance", 0) / 1000, 2),
        "moving_time_min": round(activity.get("moving_time", 0) / 60, 1),
        "elapsed_time_min": round(activity.get("elapsed_time", 0) / 60, 1),
        "elevation_gain_m": activity.get("total_elevation_gain", 0),
        "average_speed_kmh": round(activity.get("average_speed", 0) * 3.6, 2),
        "max_speed_kmh": round(activity.get("max_speed", 0) * 3.6, 2),
        "average_heartrate": activity.get("average_heartrate"),
        "max_heartrate": activity.get("max_heartrate"),
        "suffer_score": activity.get("suffer_score"),
        "calories": activity.get("calories"),
        "average_cadence": activity.get("average_cadence"),
        "average_watts": activity.get("average_watts"),
        "weighted_average_watts": activity.get("weighted_average_watts"),
        "splits_metric": activity.get("splits_metric"),
        "laps": [
            {
                "lap_index": lap.get("lap_index"),
                "distance_km": round(lap.get("distance", 0) / 1000, 2),
                "moving_time_min": round(lap.get("moving_time", 0) / 60, 1),
                "average_speed_kmh": round(lap.get("average_speed", 0) * 3.6, 2),
                "average_heartrate": lap.get("average_heartrate"),
            }
            for lap in activity.get("laps", [])
        ],
        "gear_id": activity.get("gear_id"),
    }


@mcp.tool
def get_activity_streams(
    activity_id: int,
    keys: list[str] | None = None,
) -> dict:
    """
    Retourne les streams d'une activité sous-échantillonnés à 1 point/5s.

    Résolution native Strava : 1 point/seconde.
    Résolution retournée    : 1 point/5 secondes (step=5).
    Réduction mémoire       : ~80%.
    Impact calculs           : NP ±1.4W (<1%), hrTSS <0.1, Tps Z1-Z2 <0.5%.

    IMPORTANT pour Claude — adapter les calculs au step=5 :
      - Fenêtre NP   : 6 points  (= 30s réelles)
      - dt hrTSS     : 5 secondes par point
      - dt Tps Z1-Z2 : 5 secondes par point
      - Durée totale : nb_points × 5 secondes

    Args:
        activity_id: ID Strava de l'activité.
        keys: types de streams. Défaut: time, distance, heartrate,
              velocity_smooth, altitude.
    """
    if keys is None:
        keys = ["time", "distance", "heartrate", "velocity_smooth", "altitude"]

    STEP = 5  # 1 point toutes les 5 secondes

    streams_raw = _strava_get(
        f"/activities/{activity_id}/streams",
        {"keys": ",".join(keys), "key_by_type": "true"},
    )

    # Sous-échantillonnage de chaque stream
    streams_out = {}
    for key, stream in streams_raw.items():
        if isinstance(stream, dict) and "data" in stream:
            subsampled = _subsample_stream(stream["data"], step=STEP)
            streams_out[key] = {
                **{k: v for k, v in stream.items() if k != "data"},
                "data": subsampled,
                "original_length": len(stream["data"]),
                "subsampled_length": len(subsampled),
            }
        else:
            streams_out[key] = stream

    streams_out["_meta"] = {
        "step_seconds": STEP,
        "note": (
            f"Stream sous-échantillonné à 1pt/{STEP}s. "
            f"Adapter : fenêtre NP=6pts, dt={STEP}s pour hrTSS et Tps Z1-Z2."
        ),
    }

    return streams_out


@mcp.tool
def get_athlete_stats() -> dict:
    """Retourne les stats globales de l'athlète (totaux récents et all-time)."""
    athlete = _strava_get("/athlete")
    athlete_id = athlete["id"]
    stats = _strava_get(f"/athletes/{athlete_id}/stats")
    return {
        "athlete": {
            "id": athlete_id,
            "firstname": athlete.get("firstname"),
            "weight_kg": athlete.get("weight"),
            "ftp": athlete.get("ftp"),
        },
        "recent_run_totals": stats.get("recent_run_totals"),
        "recent_ride_totals": stats.get("recent_ride_totals"),
        "ytd_run_totals": stats.get("ytd_run_totals"),
        "ytd_ride_totals": stats.get("ytd_ride_totals"),
        "all_run_totals": stats.get("all_run_totals"),
        "all_ride_totals": stats.get("all_ride_totals"),
    }


# --- Outils Google Calendar : lecture ---

@mcp.tool
def list_calendars() -> list[dict]:
    """Liste les agendas Google Calendar disponibles."""
    service = _get_calendar_service()
    result = service.calendarList().list().execute()
    return [
        {
            "id": cal.get("id"),
            "summary": cal.get("summary"),
            "primary": cal.get("primary", False),
            "access_role": cal.get("accessRole"),
            "time_zone": cal.get("timeZone"),
        }
        for cal in result.get("items", [])
    ]


@mcp.tool
def list_calendar_events(
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
    max_results: int = 20,
) -> list[dict]:
    """
    Retourne les événements dans une plage de dates.

    Args:
        time_min: début de la plage, format 'YYYY-MM-DD' ou ISO 8601.
        time_max: fin de la plage, format 'YYYY-MM-DD' ou ISO 8601.
        calendar_id: identifiant de l'agenda (défaut : 'primary').
        max_results: nombre max d'événements (défaut : 20, max : 100).
    """
    service = _get_calendar_service()
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=_to_rfc3339(time_min),
            timeMax=_to_rfc3339(time_max),
            maxResults=min(max_results, 100),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [_summarize_event(e) for e in result.get("items", [])]


@mcp.tool
def get_calendar_event(
    event_id: str,
    calendar_id: str = "primary",
) -> dict:
    """
    Retourne le détail complet d'un événement Calendar.

    Args:
        event_id: identifiant de l'événement.
        calendar_id: identifiant de l'agenda (défaut : 'primary').
    """
    service = _get_calendar_service()
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id"),
        "title": event.get("summary"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "description": event.get("description"),
        "location": event.get("location"),
        "color_id": event.get("colorId"),
        "status": event.get("status"),
        "recurrence": event.get("recurrence"),
        "reminders": event.get("reminders"),
        "attendees": [
            {
                "email": a.get("email"),
                "display_name": a.get("displayName"),
                "response_status": a.get("responseStatus"),
            }
            for a in event.get("attendees", [])
        ],
        "html_link": event.get("htmlLink"),
        "creator": event.get("creator", {}).get("email"),
        "created": event.get("created"),
        "updated": event.get("updated"),
    }


# --- Outils Google Calendar : écriture ---

_TRAINING_TYPE_COLORS = {
    "footing": "7",        # Bleu paon
    "sortie_longue": "9",  # Bleuet
    "fractionne": "11",    # Rouge tomate
    "recup": "2",          # Sauge
    "renfo": "5",          # Banane
    "competition": "6",    # Mandarine
    "repos": "8",          # Graphite
}


@mcp.tool
def create_calendar_event(
    title: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str | None = None,
    location: str | None = None,
    training_type: str | None = None,
) -> dict:
    """
    Crée un événement dans Google Calendar.

    Args:
        title: titre de l'événement.
        start: début ISO 8601 ('YYYY-MM-DD' ou 'YYYY-MM-DDTHH:MM:SS').
        end: fin ISO 8601.
        calendar_id: identifiant de l'agenda (défaut : 'primary').
        description: description libre.
        location: lieu.
        training_type: type de séance → couleur auto.
          Valeurs : footing, sortie_longue, fractionne, recup, renfo,
                    competition, repos.
    """
    def _fmt(d: str) -> dict:
        if "T" in d:
            return {"dateTime": d if d.endswith("Z") else d, "timeZone": "Europe/Paris"}
        return {"date": d}

    body: dict = {
        "summary": title,
        "start": _fmt(start),
        "end": _fmt(end),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if training_type and training_type in _TRAINING_TYPE_COLORS:
        body["colorId"] = _TRAINING_TYPE_COLORS[training_type]

    service = _get_calendar_service()
    event = service.events().insert(calendarId=calendar_id, body=body).execute()
    return {
        "id": event.get("id"),
        "title": event.get("summary"),
        "start": event.get("start", {}).get("dateTime") or event.get("start", {}).get("date"),
        "end": event.get("end", {}).get("dateTime") or event.get("end", {}).get("date"),
        "html_link": event.get("htmlLink"),
        "color_id": event.get("colorId"),
    }


@mcp.tool
def update_calendar_event(
    event_id: str,
    calendar_id: str = "primary",
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    training_type: str | None = None,
) -> dict:
    """
    Met à jour un ou plusieurs champs d'un événement existant (PATCH).

    Args:
        event_id: identifiant de l'événement.
        calendar_id: identifiant de l'agenda (défaut : 'primary').
        title, start, end, description, location, training_type: champs optionnels.
    """
    def _fmt(d: str) -> dict:
        if "T" in d:
            return {"dateTime": d, "timeZone": "Europe/Paris"}
        return {"date": d}

    body: dict = {}
    if title is not None:
        body["summary"] = title
    if start is not None:
        body["start"] = _fmt(start)
    if end is not None:
        body["end"] = _fmt(end)
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if training_type is not None and training_type in _TRAINING_TYPE_COLORS:
        body["colorId"] = _TRAINING_TYPE_COLORS[training_type]

    service = _get_calendar_service()
    event = (
        service.events()
        .patch(calendarId=calendar_id, eventId=event_id, body=body)
        .execute()
    )
    start_val = event.get("start", {})
    end_val = event.get("end", {})
    return {
        "id": event.get("id"),
        "title": event.get("summary"),
        "start": start_val.get("dateTime") or start_val.get("date"),
        "end": end_val.get("dateTime") or end_val.get("date"),
        "html_link": event.get("htmlLink"),
        "color_id": event.get("colorId"),
        "updated": event.get("updated"),
    }


@mcp.tool
def delete_calendar_event(
    event_id: str,
    calendar_id: str = "primary",
) -> dict:
    """
    Supprime un événement Google Calendar.

    Args:
        event_id: identifiant de l'événement.
        calendar_id: identifiant de l'agenda (défaut : 'primary').
    """
    service = _get_calendar_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return {"deleted": True, "event_id": event_id}


# ============================================================
# GOOGLE SHEETS TOOLS
# ============================================================

@mcp.tool()
def get_sheet_values(spreadsheet_id: str, range_name: str) -> str:
    """Lit une plage de cellules dans un Google Sheet.

    Args:
        spreadsheet_id: ID du spreadsheet (dans l'URL après /d/)
        range_name: Plage au format A1 ex: 'SÉANCES!A1:N100'
    """
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()
        values = result.get("values", [])
        return json.dumps(values, ensure_ascii=False)
    except Exception as e:
        return f"Erreur: {str(e)}"


@mcp.tool()
def update_sheet_values(spreadsheet_id: str, range_name: str, values: list) -> str:
    """Écrit des valeurs dans une plage de cellules.

    Args:
        spreadsheet_id: ID du spreadsheet
        range_name: Plage cible ex: 'SÉANCES!A3:N3'
        values: Liste de listes [[row1col1, row1col2], [row2col1, ...]]
    """
    try:
        service = get_sheets_service()
        body = {"values": values}
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        return f"OK — {result.get('updatedCells')} cellules mises à jour"
    except Exception as e:
        return f"Erreur: {str(e)}"


@mcp.tool()
def append_sheet_row(spreadsheet_id: str, sheet_name: str, values: list) -> str:
    """Ajoute une ligne à la fin d'un onglet.

    Args:
        spreadsheet_id: ID du spreadsheet
        sheet_name: Nom de l'onglet ex: 'SÉANCES'
        values: Liste de valeurs [col1, col2, ...]
    """
    try:
        service = get_sheets_service()
        body = {"values": [values]}
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()
        return f"OK — ligne ajoutée dans {sheet_name}"
    except Exception as e:
        return f"Erreur: {str(e)}"


@mcp.tool()
def get_spreadsheet_info(spreadsheet_id: str) -> str:
    """Retourne les métadonnées d'un spreadsheet (titre, liste des onglets).

    Args:
        spreadsheet_id: ID du spreadsheet
    """
    try:
        service = get_sheets_service()
        result = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()
        sheets = [s["properties"]["title"] for s in result.get("sheets", [])]
        return json.dumps({
            "title": result.get("properties", {}).get("title"),
            "sheets": sheets
        }, ensure_ascii=False)
    except Exception as e:
        return f"Erreur: {str(e)}"


# --- Health check endpoint (pour UptimeRobot / keep-alive) ---
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

async def health(request):
    return JSONResponse({"status": "ok", "service": "strava-coach-mcp"})

# Monter le health check sur l'app Starlette sous-jacente de FastMCP
def create_app():
    mcp_app = mcp.http_app(path="/mcp")
    app = Starlette(
        routes=[Route("/health", health)],
    )
    app.mount("/", mcp_app)
    return app

# --- Lancement serveur ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
