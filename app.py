from fastmcp import FastMCP
from openai import OpenAI
from flask import Flask
import requests
import os

# --- Initialisation ---
app_http = Flask(__name__)
mcp = FastMCP("Strava Coach")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Endpoint HTTP simple pour test navigateur ---
@app_http.route("/analyze")
def analyze_http():
    try:
        print("Analyse appelée")
        return analyze_last_activity()
    except Exception as e:
        return {"error": str(e)}

# --- Auth Strava ---
def get_access_token():
    response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "refresh_token": os.getenv("STRAVA_REFRESH_TOKEN"),
            "grant_type": "refresh_token"
        }
    )
    data = response.json()
    return data.get("access_token")

# --- Récupération activités ---
@mcp.tool
def get_recent_activities():
    token = get_access_token()

    if not token:
        return {"error": "Impossible de récupérer le token Strava"}

    response = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"}
    )

    return response.json()

# --- Analyse avec GPT + fallback ---
@mcp.tool
def analyze_last_activity():
    activities = get_recent_activities()

    if not activities or isinstance(activities, dict):
        return {"error": "Aucune activité trouvée ou erreur Strava"}

    last = activities[0]

    distance = round(last.get("distance", 0) / 1000, 2)
    duration = round(last.get("moving_time", 0) / 60, 1)
    elevation = last.get("total_elevation_gain", 0)

    prompt = f"""
    Tu es un coach trail expert.

    Analyse cette sortie :
    - Distance : {distance} km
    - Durée : {duration} minutes
    - D+ : {elevation} m

    Donne :
    1. Un feedback rapide
    2. Un point fort
    3. Un point d’amélioration
    4. Une recommandation pour la prochaine séance

    Réponse courte et actionnable.
    """

    # --- Appel GPT avec fallback ---
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        analysis = response.choices[0].message.content

    except Exception:
        # 👉 Fallback propre (pas de message technique)
        analysis = (
            "Analyse indisponible (quota API ou connexion). "
            "Sortie longue et régulière, bon travail d’endurance. "
            "Continue à surveiller la récupération avant la prochaine séance."
        )

    return {
        "distance_km": distance,
        "duration_min": duration,
        "elevation_m": elevation,
        "analysis": analysis
    }

# --- Lancement serveur ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app_http.run(host="0.0.0.0", port=port)
