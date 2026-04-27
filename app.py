from fastmcp import FastMCP
from openai import OpenAI
import requests
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

mcp = FastMCP("Strava Coach")

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
    return response.json()["access_token"]

@mcp.tool
def get_recent_activities():
    token = get_access_token()

    response = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"}
    )

    return response.json()

@mcp.tool
def analyze_last_activity():
    activities = get_recent_activities()
    
    if not activities:
        return {"error": "No activities found"}

    last = activities[0]

    distance = round(last["distance"] / 1000, 2)
    duration = round(last["moving_time"] / 60, 1)
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

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    return {
        "distance_km": distance,
        "duration_min": duration,
        "elevation_m": elevation,
        "analysis": response.choices[0].message.content
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    mcp.run(transport="http", host="0.0.0.0", port=port)
