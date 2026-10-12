"""Tests for the OpenBao / Vault client shared by the MAAS services"""

import hvac
import pytest

from maas_model.vault import (
    ConnectionSettings,
    VaultClient,
    VaultSettings,
    read_connection,
)

OPENSEARCH = {
    "url": "https://opensearch.example:9200",
    "username": "maas",
    "password": "s3cr3t-opensearch",
}


@pytest.fixture
def settings(fake_vault, tmp_path):
    """AppRole settings for the fake vault, secret id in a file like a Swarm secret"""
    secret_id_file = tmp_path / "secret_id"
    # secret files usually end with a new line
    secret_id_file.write_text(fake_vault.secret_id + "\n", encoding="UTF-8")

    return VaultSettings(
        url=fake_vault.url,
        role_id=fake_vault.role_id,
        secret_id_file=str(secret_id_file),
    )


def test_read_secret_returns_the_latest_version(fake_vault, settings):
    fake_vault.put("secret", "maas/cds/opensearch", {"url": "http://old"})
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)

    assert VaultClient(settings).read_secret("maas/cds/opensearch") == OPENSEARCH


def test_read_secret_reads_under_the_configured_mount(fake_vault, settings):
    fake_vault.put("kv", "maas/cds/opensearch", OPENSEARCH)
    fake_vault.put("secret", "maas/cds/opensearch", {"url": "http://wrong"})
    settings.kv_mount = "kv"

    assert VaultClient(settings).read_secret("maas/cds/opensearch") == OPENSEARCH


def test_read_secret_returns_none_without_secret(fake_vault, settings):
    assert VaultClient(settings).read_secret("maas/cds/opensearch") is None


def test_read_secret_keeps_its_token_between_reads(fake_vault, settings):
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)
    client = VaultClient(settings)

    client.read_secret("maas/cds/opensearch")
    client.read_secret("maas/cds/opensearch")

    assert fake_vault.logins == 1


def test_read_secret_logs_in_again_when_its_token_expired(fake_vault, settings):
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)
    client = VaultClient(settings)
    client.read_secret("maas/cds/opensearch")

    fake_vault.expire_tokens()

    assert client.read_secret("maas/cds/opensearch") == OPENSEARCH
    assert fake_vault.logins == 2


def test_read_secret_does_not_log_in_again_when_the_policy_denies_it(
    fake_vault, settings
):
    """a valid token refused by the policy is not an expired token: a new login
    would be refused the same way"""
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)
    fake_vault.denied.add("secret/maas/cds/opensearch")
    client = VaultClient(settings)

    for _ in range(3):
        with pytest.raises(hvac.exceptions.Forbidden):
            client.read_secret("maas/cds/opensearch")

    assert fake_vault.logins == 1


def test_read_secret_gives_up_after_one_new_login(fake_vault, settings):
    """an expired token followed by a policy denial must not loop on logins"""
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)
    client = VaultClient(settings)
    client.read_secret("maas/cds/opensearch")

    fake_vault.expire_tokens()
    fake_vault.denied.add("secret/maas/cds/opensearch")

    with pytest.raises(hvac.exceptions.Forbidden):
        client.read_secret("maas/cds/opensearch")

    assert fake_vault.logins == 2


def test_read_secret_raises_when_the_secret_id_is_refused(fake_vault, settings):
    fake_vault.secret_id = "revoked"

    with pytest.raises(hvac.exceptions.InvalidRequest):
        VaultClient(settings).read_secret("maas/cds/opensearch")


def test_read_secret_rereads_the_secret_id_at_each_login(fake_vault, settings):
    """a secret id rotated in its file is used without restart"""
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)
    client = VaultClient(settings)
    client.read_secret("maas/cds/opensearch")

    fake_vault.secret_id = "rotated-secret-id"
    with open(settings.secret_id_file, "w", encoding="UTF-8") as secret_id_fd:
        secret_id_fd.write("rotated-secret-id\n")
    fake_vault.expire_tokens()

    assert client.read_secret("maas/cds/opensearch") == OPENSEARCH


def test_token_authentication_with_a_token_file(fake_vault, tmp_path):
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)
    token_file = tmp_path / "token"
    token_file.write_text(fake_vault.root_token + "\n", encoding="UTF-8")

    client = VaultClient(
        VaultSettings(
            url=fake_vault.url, auth_method="token", token_file=str(token_file)
        )
    )

    assert client.read_secret("maas/cds/opensearch") == OPENSEARCH


def test_token_authentication_needs_a_token(fake_vault, monkeypatch, tmp_path):
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    # hvac also reads ~/.vault-token
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ValueError, match="VAULT_TOKEN.*VAULT_TOKEN_FILE"):
        VaultClient(VaultSettings(url=fake_vault.url, auth_method="token"))


def test_token_authentication_with_vault_token(fake_vault, monkeypatch):
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)
    monkeypatch.setenv("VAULT_TOKEN", fake_vault.root_token)

    client = VaultClient(VaultSettings(url=fake_vault.url, auth_method="token"))

    assert client.read_secret("maas/cds/opensearch") == OPENSEARCH


@pytest.mark.parametrize(
    "settings, setting",
    [
        (VaultSettings(url="", role_id="r", secret_id_file="f"), "VAULT_ADDR"),
        (
            VaultSettings(url="http://v", role_id="", secret_id_file="f"),
            "VAULT_ROLE_ID",
        ),
        (
            VaultSettings(url="http://v", role_id="r", secret_id_file=""),
            "VAULT_SECRET_ID_FILE",
        ),
        (VaultSettings(url="http://v", auth_method="kerberos"), "VAULT_AUTH_METHOD"),
    ],
)
def test_client_names_the_missing_setting(settings, setting):
    with pytest.raises(ValueError, match=setting):
        VaultClient(settings)


def test_read_connection_returns_the_connection(fake_vault, settings):
    fake_vault.put("secret", "maas/cds/opensearch", OPENSEARCH)

    connection = read_connection(
        VaultClient(settings), "maas/cds/opensearch", "OpenSearch"
    )

    assert connection == ConnectionSettings(
        url="https://opensearch.example:9200",
        username="maas",
        password="s3cr3t-opensearch",
    )


def test_read_connection_without_authentication(fake_vault, settings):
    fake_vault.put("secret", "maas/cds/amqp", {"url": "amqp://rabbit:5672//"})

    connection = read_connection(VaultClient(settings), "maas/cds/amqp", "AMQP")

    assert connection == ConnectionSettings(url="amqp://rabbit:5672//")


def test_read_connection_raises_without_secret(fake_vault, settings):
    with pytest.raises(ValueError, match="AMQP.*secret/maas/cds/amqp"):
        read_connection(VaultClient(settings), "maas/cds/amqp", "AMQP")


def test_read_connection_raises_without_url(fake_vault, settings):
    fake_vault.put(
        "secret", "maas/cds/amqp", {"username": "maas", "password": "s3cr3t-amqp"}
    )

    with pytest.raises(ValueError, match="url") as error:
        read_connection(VaultClient(settings), "maas/cds/amqp", "AMQP")

    assert "s3cr3t-amqp" not in str(error.value)


def test_connection_repr_hides_the_password():
    connection = ConnectionSettings(
        url="https://opensearch.example:9200",
        username="maas",
        password="s3cr3t-opensearch",
    )

    assert "s3cr3t-opensearch" not in repr(connection)
