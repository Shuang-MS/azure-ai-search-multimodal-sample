import logging
import os

from azure.identity.aio import AzureCliCredential, DefaultAzureCredential


def build_token_credential():
    """Return the right credential for the environment.

    DefaultAzureCredential works for App Service (managed identity) and most local
    setups. Allow opting into AzureCliCredential for local debugging by setting
    USE_AZ_CLI_CREDENTIAL=true.
    """

    use_cli = os.getenv("USE_AZ_CLI_CREDENTIAL", "").lower() in ("1", "true", "yes")
    if use_cli:
        logging.info("Using AzureCliCredential (USE_AZ_CLI_CREDENTIAL enabled).")
        return AzureCliCredential()

    logging.info("Using DefaultAzureCredential.")
    return DefaultAzureCredential(
        exclude_interactive_browser_credential=True,
    )
