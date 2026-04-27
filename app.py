from fastmcp import FastMCP
import requests
import os

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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    mcp.run(transport="http", host="0.0.0.0", port=port)
