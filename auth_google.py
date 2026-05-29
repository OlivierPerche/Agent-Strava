"""
Script d'autorisation Google Calendar (usage unique).
Lance-le une fois pour obtenir le GOOGLE_REFRESH_TOKEN à copier dans Render.

PRÉREQUIS :
  1. Google Cloud Console → créer un projet
  2. Activer l'API "Google Calendar API"
  3. Créer des identifiants OAuth 2.0 → type "Application de bureau" (Desktop app)
  4. Télécharger le JSON → renommer en "credentials.json"
  5. Placer "credentials.json" dans le MÊME dossier que ce script
  6. Installer la dépendance si besoin : pip install google-auth-oauthlib
  7. Lancer : python auth_google.py

Le navigateur s'ouvre → connecte-toi et autorise l'accès → reviens ici.
Les 3 valeurs à copier dans Render s'affichent dans le terminal.
"""

from google_auth_oauthlib.flow import InstalledAppFlow
import json
import os

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = "credentials.json"


def main():
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"[ERREUR] '{CREDENTIALS_FILE}' introuvable dans ce dossier.")
        print("  → Télécharge-le depuis Google Cloud Console > APIs & Services > Identifiants")
        print("  → Renomme-le 'credentials.json' et place-le ici.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    # Extraire client_id et client_secret directement depuis le fichier
    with open(CREDENTIALS_FILE) as f:
        raw = json.load(f)
    client = raw.get("installed") or raw.get("web", {})

    print("\n" + "=" * 60)
    print("  AUTORISATION RÉUSSIE — copie ces 3 valeurs dans Render")
    print("=" * 60)
    print(f"\n  GOOGLE_CLIENT_ID     = {client.get('client_id', '(non trouvé)')}")
    print(f"  GOOGLE_CLIENT_SECRET = {client.get('client_secret', '(non trouvé)')}")
    print(f"  GOOGLE_REFRESH_TOKEN = {creds.refresh_token}")
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
