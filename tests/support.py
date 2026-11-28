"""Shared test fixtures for the V3 instance-wide AWS connection (synthetic values only)."""

MODEL = "amazon.nova-lite-v1:0"
PASSPHRASE = "synthetic-shared-vault-passphrase"
CONNECTION = {"region": "us-east-1", "model": MODEL, "access_key_id": "AKIASYNTHETIC0000001",
              "secret_access_key": "synthetic-secret-access-key", "session_token": None}


def ready_shared_vault(app, connection=CONNECTION):
    """Create (or unlock after a restart) the app's shared vault and save a synthetic connection."""
    vault = app.extensions["sous_vault"]
    if vault.initialized:
        vault.unlock(PASSPHRASE)
    else:
        vault.create(PASSPHRASE)
    if connection is not None and vault.bedrock() is None:
        vault.save_bedrock(dict(connection))
    return vault
