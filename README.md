# Conference Translator

App web de traduction temps réel pour conférence :

- détection automatique `anglais <-> français`
- traduction en sens inverse selon la langue détectée

Le flux est :

1. micro navigateur
2. `voxtral-mini-transcribe-realtime-2602`
3. traduction texte avec `mistral-small-latest`
4. affichage plein écran sur un écran de salle

Le système n'a plus de bascule manuelle :

- si une phrase est détectée en anglais, elle est affichée en français
- si une phrase est détectée en français, elle est affichée en anglais

## Architecture recommandée

- `WordPress` :
  pages publiques, programme, boutons d'accès
- `live.votresite.com` :
  app de traduction hébergée
- `Mistral API` :
  transcription realtime + traduction

WordPress ne porte pas le temps réel. Il sert juste de point d'entrée.

Exemple de liens :

- console opérateur :
  `https://live.votresite.com/control/main-stage`
- écran public :
  `https://live.votresite.com/display/main-stage`

Vous pouvez mettre ces liens dans WordPress sur une page session.

## URLs de l'application

- opérateur :
  `/control/{session_id}`
- affichage public :
  `/display/{session_id}`
- santé :
  `/healthz`

Exemple local :

- [http://localhost:8000/control/default](http://localhost:8000/control/default)
- [http://localhost:8000/display/default](http://localhost:8000/display/default)

## Installation ultra simple sur Mac

1. Double-cliquez sur [Installer.command](/Users/stephanebayle/Documents/New%20project/Installer.command)
2. Double-cliquez sur [Configurer-cle.command](/Users/stephanebayle/Documents/New%20project/Configurer-cle.command)
3. Collez votre clé Mistral dans `.env`
4. Double-cliquez sur [Lancer.command](/Users/stephanebayle/Documents/New%20project/Lancer.command)

Le lanceur ouvre :

- `http://localhost:8000/control/default`

Pour projeter l'écran public sur un second écran :

- ouvrez aussi `http://localhost:8000/display/default`

## Installation manuelle

```bash
rm -rf .venv
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Puis renseignez `MISTRAL_API_KEY` dans `.env`.

## Lancement manuel

```bash
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8000
```

Puis ouvrez :

- [http://localhost:8000/control/default](http://localhost:8000/control/default)
- [http://localhost:8000/display/default](http://localhost:8000/display/default)

## Déploiement hébergé simple

Le projet inclut [render.yaml](/Users/stephanebayle/Documents/New%20project/render.yaml) pour un déploiement Render.

### Côté hébergeur

1. poussez le dépôt sur GitHub
2. créez un Web Service Render depuis ce dépôt
3. laissez Render lire `render.yaml`
4. définissez `MISTRAL_API_KEY` dans les variables secrètes
5. ajoutez le domaine `live.votresite.com`

### Côté DNS

Créez un `CNAME` :

- `live` -> cible fournie par Render

### Côté WordPress

Créez une page par session avec 2 liens :

- `Lancer la console`
- `Ouvrir l'écran public`

Exemple :

- `https://live.votresite.com/control/paris-main-stage`
- `https://live.votresite.com/display/paris-main-stage`

## Utilisation en salle

1. branchez le portable à l'écran ou au vidéoprojecteur
2. ouvrez la console opérateur
3. ouvrez l'écran public sur la même `session_id`
4. sélectionnez l'entrée micro correcte
5. cliquez sur `Démarrer`
6. laissez la détection automatique choisir le sens de traduction
7. mettez l'écran public en plein écran

## Réglages utiles

- `MISTRAL_TRANSCRIBE_MODEL=voxtral-mini-transcribe-realtime-2602`
- `MISTRAL_TRANSLATE_MODEL=mistral-small-latest`
- `MISTRAL_TARGET_DELAY_MS=900`
- `MISTRAL_SAMPLE_RATE=16000`
- `TRANSLATION_IDLE_FLUSH_MS=550`
- `TRANSLATION_MIN_CHARS=4`

## Limites actuelles

- une seule console opérateur active par session est recommandée
- état de session stocké en mémoire du serveur
- pas de séparation automatique des locuteurs
- pas de traduction audio directe, seulement transcription puis traduction texte
- pour un usage scène, un signal venant de la régie sera meilleur que le micro ambiant du portable
