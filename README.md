# Agent-Strava

Serveur MCP (Model Context Protocol) qui expose des outils Strava et Google Calendar à Claude.
Permet à Claude de lire tes activités sportives et de gérer ton agenda d'entraînement.

Déployé sur Render en mode Streamable HTTP — auto-deploy à chaque push sur `main`.

---

## Outils MCP exposés

### Strava (5 outils)

| Outil | Description |
|---|---|
| `get_recent_activities` | N dernières activités (résumé) |
| `get_activities_by_date_range` | Activités entre deux dates |
| `get_activity_details` | Détail complet : splits, laps, FC, watts, calories |
| `get_activity_streams` | Séries temporelles seconde par seconde |
| `get_athlete_stats` | Totaux globaux run/vélo (récent, YTD, all-time) |

### Google Calendar (6 outils)

| Outil | Description |
|---|---|
| `list_calendars` | Liste les agendas disponibles et leurs `calendar_id` |
| `list_calendar_events` | Événements dans une plage de dates |
| `get_calendar_event` | Détail complet d'un événement |
| `create_calendar_event` | Crée une séance (avec couleur auto via `training_type`) |
| `update_calendar_event` | Met à jour un ou plusieurs champs (PATCH) |
| `delete_calendar_event` | Supprime un événement |

#### Mapping `training_type` → couleur Google Calendar

| Valeur | Couleur |
|---|---|
| `footing` | Bleu paon |
| `sortie_longue` | Bleuet |
| `fractionne` | Rouge tomate |
| `recup` | Sauge (vert) |
| `renfo` | Banane (jaune) |
| `competition` | Mandarine |
| `repos` | Graphite |

---

## Variables d'environnement

À configurer dans Render > Environment :

| Variable | Description |
|---|---|
| `STRAVA_CLIENT_ID` | ID de l'application Strava |
| `STRAVA_CLIENT_SECRET` | Secret de l'application Strava |
| `STRAVA_REFRESH_TOKEN` | Refresh token Strava (obtenu via le flow OAuth Strava) |
| `GOOGLE_CLIENT_ID` | ID OAuth2 Google (depuis `credentials.json`) |
| `GOOGLE_CLIENT_SECRET` | Secret OAuth2 Google (depuis `credentials.json`) |
| `GOOGLE_REFRESH_TOKEN` | Refresh token Google (obtenu via `auth_google.py`) |

---

## Générer le refresh token Google

1. [Google Cloud Console](https://console.cloud.google.com/) → créer un projet
2. Activer l'API **Google Calendar API**
3. Créer des identifiants OAuth 2.0 → type **Application de bureau**
4. Télécharger le JSON → renommer en `credentials.json`, placer à la racine du projet
5. Installer la dépendance : `pip install google-auth-oauthlib`
6. Lancer : `python auth_google.py`
7. Le navigateur s'ouvre → autoriser l'accès
8. Copier les 3 valeurs affichées dans le terminal (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`) dans Render

> `credentials.json` ne doit pas être commité — ajoute-le à `.gitignore`.

---

## Déploiement Render

- **Type** : Web Service
- **Build command** : `pip install -r requirements.txt`
- **Start command** : `python app.py`
- **URL MCP** : `https://<ton-service>.onrender.com/mcp`
- Auto-deploy activé sur push vers `main`
