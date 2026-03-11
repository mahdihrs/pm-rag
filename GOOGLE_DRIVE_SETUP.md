# Google Drive Setup Guide

Follow these steps to connect your Google Drive to PM RAG.

---

## Step 1 — Create a Google Cloud Project

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Click **New Project** → name it `pm-rag` → click **Create**
3. Select your new project from the top dropdown

---

## Step 2 — Enable the Drive API

1. In the left sidebar: **APIs & Services** → **Library**
2. Search for **Google Drive API**
3. Click it → click **Enable**

---

## Step 3 — Create OAuth Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **+ Create Credentials** → **OAuth client ID**
3. If prompted, configure the **OAuth consent screen** first:
   - User Type: **External**
   - App name: `PM RAG`
   - Add your email as a test user
   - Scopes: add `https://www.googleapis.com/auth/drive.readonly`
4. Back in Credentials → **Application type: Desktop app**
5. Name: `PM RAG CLI`
6. Click **Create**
7. Click **Download JSON** → save as `credentials.json` in your `pm-rag/` folder

---

## Step 4 — Enable Google Drive in config.yaml

```yaml
google_drive:
  enabled: true   # Change false → true
  credentials_file: "credentials.json"
  token_file: "token.json"
```

---

## Step 5 — Authenticate (first run only)

```bash
python ingest.py
```

A browser window will open asking you to sign in to Google and grant read-only Drive access. After approving, a `token.json` file is saved — you won't need to authenticate again.

---

## Limiting to Specific Folders (Optional)

Instead of syncing all of Drive, target specific folders:

1. Open the folder in Google Drive
2. The URL will look like: `https://drive.google.com/drive/folders/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs`
3. Copy the ID at the end
4. Add it to `config.yaml`:

```yaml
google_drive:
  enabled: true
  folders:
    - name: "PM Projects"
      id: "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs"
    - name: "MoMs 2024"
      id: "another_folder_id_here"
```

---

## Security Notes

- `credentials.json` and `token.json` are in `.gitignore` — never commit these
- Access is **read-only** — PM RAG cannot modify your Drive files
- To revoke access: [https://myaccount.google.com/permissions](https://myaccount.google.com/permissions)
