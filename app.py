"""
Strava MCP Server
Expose des outils Strava à Claude via MCP (Streamable HTTP).
"""
from fastmcp import FastMCP
import requests
import os
import time
from datetime import datetime, timezone

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
    # On garde l'essentiel utile à l'analyse
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
        "splits_metric": activity.get("splits_metric"),  # splits au km
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
    Retourne les streams (séries temporelles seconde par seconde) d'une activité.
    Utile pour analyser fractionnés, dérive cardiaque, etc.
    Args:
        activity_id: ID Strava de l'activité.
        keys: types de streams. Défaut: time, distance, heartrate, velocity_smooth, altitude.
              Autres: cadence, watts, temp, moving, grade_smooth.
    """
    if keys is None:
        keys = ["time", "distance", "heartrate", "velocity_smooth", "altitude"]
    streams = _strava_get(
        f"/activities/{activity_id}/streams",
        {"keys": ",".join(keys), "key_by_type": "true"},
    )
    return streams


@mcp.tool
def get_athlete_stats() -> dict:
    """
    Retourne les stats globales de l'athlète (totaux récents et all-time).
    """
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


# --- Lancement serveur ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path="/mcp")
