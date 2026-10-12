"""
OpenBao / HashiCorp Vault access shared by the MAAS services.

Install with the vault extra: pip install maas-model[vault]
"""

import dataclasses
import logging

import hvac

LOGGER = logging.getLogger("Vault")


@dataclasses.dataclass
class VaultSettings:
    """OpenBao / HashiCorp Vault connection settings"""

    url: str = ""

    # mount point of the KV version 2 secret engine
    kv_mount: str = "secret"

    # approle or token
    auth_method: str = "approle"

    role_id: str = ""

    # file holding the AppRole secret id, typically a Swarm secret
    secret_id_file: str = ""

    # file holding a token for the token auth method. Without it the token is
    # taken from VAULT_TOKEN
    token_file: str = ""


@dataclasses.dataclass
class ConnectionSettings:
    """Where and how to connect to a service, like OpenSearch or AMQP"""

    url: str

    username: str = ""

    password: str = dataclasses.field(default="", repr=False)


def read_secret_file(path: str) -> str:
    """Read a secret stored alone in a file, without the trailing new line"""
    with open(path, encoding="UTF-8") as secret_fd:
        return secret_fd.read().strip()


class VaultClient:
    """Reads secrets of an OpenBao or HashiCorp Vault KV version 2 engine"""

    AUTH_METHODS = ("approle", "token")

    def __init__(self, settings: VaultSettings):
        if not settings.url:
            raise ValueError("Vault access needs a vault address: set VAULT_ADDR")

        if settings.auth_method not in self.AUTH_METHODS:
            raise ValueError(
                f"Unknown VAULT_AUTH_METHOD {settings.auth_method}:"
                f" expected one of {', '.join(self.AUTH_METHODS)}"
            )

        if settings.auth_method == "approle":
            if not settings.role_id:
                raise ValueError("Vault AppRole authentication needs VAULT_ROLE_ID")

            if not settings.secret_id_file:
                raise ValueError(
                    "Vault AppRole authentication needs VAULT_SECRET_ID_FILE"
                )

        self.settings = settings

        # TLS verification follows VAULT_CACERT / VAULT_CAPATH like the vault CLI.
        # The token comes from VAULT_TOKEN or ~/.vault-token, like the vault CLI
        self.client = hvac.Client(url=settings.url)

        if (
            settings.auth_method == "token"
            and not settings.token_file
            and not self.client.token
        ):
            raise ValueError(
                "Vault token authentication needs VAULT_TOKEN or VAULT_TOKEN_FILE"
            )

        self._logged_in = False

    def login(self):
        """Get a new vault token"""
        if self.settings.auth_method == "approle":
            LOGGER.info("Logging in to %s with AppRole", self.settings.url)
            # read at each login so a new secret id is used without restart
            self.client.auth.approle.login(
                role_id=self.settings.role_id,
                secret_id=read_secret_file(self.settings.secret_id_file),
            )
        elif self.settings.token_file:
            self.client.token = read_secret_file(self.settings.token_file)

        self._logged_in = True

    def read_secret(self, path: str) -> dict | None:
        """Read the latest version of a secret

        Args:
            path (str): path of the secret in the KV engine, without the mount point

        Raises:
            hvac.exceptions.Forbidden: when the policy does not allow the read
            hvac.exceptions.VaultError: when the vault refuses or fails the read

        Returns:
            dict | None: the secret data, None when there is no such secret
        """
        if not self._logged_in:
            self.login()

        try:
            return self._read(path)

        except hvac.exceptions.Forbidden:
            # a valid token refused by the policy: a new login would be refused too
            if self.client.is_authenticated():
                raise

            LOGGER.info("Vault token expired, logging in again")
            self.login()
            return self._read(path)

    def _read(self, path: str) -> dict | None:
        try:
            response = self.client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=self.settings.kv_mount,
                raise_on_deleted_version=True,
            )
        except hvac.exceptions.InvalidPath:
            LOGGER.debug("No vault secret %s/%s", self.settings.kv_mount, path)
            return None

        return response["data"]["data"]


def read_connection(client: VaultClient, path: str, service: str) -> ConnectionSettings:
    """Read the connection settings of a service from a vault secret

    The secret holds url and, when the service needs authentication, username and
    password.

    Args:
        client (VaultClient): vault client
        path (str): path of the secret in the KV engine
        service (str): service name, for the error messages

    Raises:
        ValueError: when there is no secret, or the secret has no url

    Returns:
        ConnectionSettings: connection settings
    """
    secret = client.read_secret(path)

    location = f"{client.settings.kv_mount}/{path}"

    if secret is None:
        raise ValueError(f"No {service} connection secret {location} in the vault")

    if not secret.get("url"):
        raise ValueError(f"The {service} connection secret {location} has no url")

    return ConnectionSettings(
        url=secret["url"],
        username=secret.get("username") or "",
        password=secret.get("password") or "",
    )
