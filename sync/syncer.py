import json
import logging
import re
import ssl

import paho.mqtt.client as mqtt
from configparser import ConfigParser
from typing import Dict, Any, List
from sync.bundler import ResourceBundler

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class ResourceSyncer:
    """
    A class for synchronizing resources using MQTT.

    :param bundler: An instance of ResourceBundler for fetching resources.
    :type bundler: ResourceBundler
    :param config: A ConfigParser object containing configuration settings.
    :type config: ConfigParser
    """

    def __init__(self, bundler: ResourceBundler, config: ConfigParser) -> None:
        self.bundler: ResourceBundler = bundler
        self.mqtt_tls_enabled: bool = config.getboolean('notifications', 'tls_enabled', fallback=False)
        self.mqtt_ca_file: str = config.get('notifications', 'ca_file', fallback=config.get(
            'common', 'ca_file', fallback=None))
        self.mqtt_rfc5280: bool = config.getboolean('notifications', 'rfc5280', fallback=config.getboolean(
            'common', 'rfc5280', fallback=True))
        self.mqtt_host: str = config.get('notifications', 'server')
        self.mqtt_port: int = int(config.get('notifications', 'port', fallback=1883 if not self.mqtt_tls_enabled else 8883))
        self.mqtt_topic: str = config.get('notifications', 'topic')
        self.mqtt_username: str = config.get('notifications', 'username', fallback=None)
        self.mqtt_password: str = config.get('notifications', 'password', fallback=None)
        self.mqtt_client: mqtt.Client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.setup()

    def setup(self) -> None:
        """
        Set up the MQTT client with the configured settings.
        """
        if self.mqtt_username and self.mqtt_password:
            self.mqtt_client.username_pw_set(self.mqtt_username, self.mqtt_password)
        if self.mqtt_tls_enabled:
            context = ssl.create_default_context(cafile=self.mqtt_ca_file)
            if self.mqtt_rfc5280:
                context.verify_flags |= ssl.VERIFY_X509_STRICT
            else:
                context.verify_flags &= ~ssl.VERIFY_X509_STRICT
            self.mqtt_client.tls_set_context(context)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message

    def run(self) -> None:
        """
        Connect to the MQTT server and process incoming messages until the process is stopped.
        """
        self.mqtt_client.connect(self.mqtt_host, self.mqtt_port, 60)
        self.mqtt_client.loop_forever()

    def on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, int], reason_code: int,
                   properties: Any) -> None:
        """
        Callback function for when the client receives a CONNACK response from the server.

        :param client: The client instance for this callback.
        :type client: mqtt.Client
        :param userdata: The private user data as set in Client() or userdata_set().
        :type userdata: Any
        :param flags: Response flags sent by the broker.
        :type flags: Dict[str, int]
        :param reason_code: The connection result.
        :type reason_code: int
        :param properties: The MQTT v5.0 properties returned from the broker.
        :type properties: Any
        """
        logger.info(f"MQTT client connected to {self.mqtt_host}:{self.mqtt_port} ({str(reason_code)})")
        client.subscribe(f"{self.mqtt_topic}/#")

    def on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """
        Callback function for when a PUBLISH message is received from the server.

        :param client: The client instance for this callback.
        :type client: mqtt.Client
        :param userdata: The private user data as set in Client() or userdata_set().
        :type userdata: Any
        :param msg: An instance of MQTTMessage containing topic and payload.
        :type msg: mqtt.MQTTMessage
        """
        logger.debug(f"New message on topic {msg.topic}: {msg.payload}")
        self.process_message(json.loads(msg.payload))

    def process_message(self, message: Dict[str, Any]) -> None:
        """
        Process the received message.

        :param message: A dictionary containing the message data.
        :type message: Dict[str, Any]
        """
        pass


class ConsentSyncer(ResourceSyncer):
    """
    A class for synchronizing consent resources, inheriting from ResourceSyncer.

    :param bundler: An instance of ResourceBundler for fetching resources.
    :type bundler: ResourceBundler
    :param config: A ConfigParser object containing configuration settings.
    :type config: ConfigParser
    """

    def __init__(self, bundler: ResourceBundler, config: ConfigParser) -> None:
        super().__init__(bundler, config)

    def process_message(self, message: Dict[str, Any]) -> None:
        """
        Process the received message for consent synchronization.

        :param message: A dictionary containing the message data.
        :type message: Dict[str, Any]
        """
        #if message.get("data", {}) == "GICS.SetQcForConsent":
        logger.debug(f"Received message: {message}")
        message_data: Dict[str, Any] = json.loads(message.get("data", {}))
        if message_data.get('type') == "GICS.policyValidityStatusChanged":
            affectedPolicies = message_data.get("affectedPolicies")
            signerIds = []
            for ap in affectedPolicies:
                signerIds.append(f"{ap.get("signerIdType")} {ap.get("signerId")}")
            logger.debug(f"Ignoring {message_data.get("type")} for {','.join(signerIds)}")
        else:
            consent_key: Dict[str, Any] = message_data.get("consentKey") or {}
            logger.debug(f"Consent key is {consent_key}")
            consent_template_key: str = (consent_key.get("consentTemplateKey") or {}).get("domainName")
            logger.debug(f"Consent template key is {consent_template_key}")
            signer_ids: List[Dict[str, str]] = consent_key.get("signerIds")
            logger.debug(f"Signer IDs are {signer_ids}")
            if consent_template_key and signer_ids:
                for signer_id in signer_ids:
                    if not re.fullmatch(r'^[0-9]{10}$', signer_id.get('id')):
                        logger.warning(f"Invalid Signer ID: {signer_id.get('id')}. Must be exactly 10 digits.")
                    else:
                        if signer_id and 'context' in message_data:
                            if message_data.get("context").get("qc").get("qcPassed"):
                                self.fetch_fhir_consents(sid=signer_id.get("id"), sid_type=signer_id.get("idType"),
                                                     consent_template_key=consent_template_key)
                            else:
                                #TODO: deletion of invalidated or otherwise invalid Consent resources.
                                logger.info(f"Consent QC is not passed with type '{message_data.get("context").get("qc").get("type")}'.")
            else:
                logger.warning(f"Ignoring {message_data.get('type')} message without consent template key or "
                               f"signer IDs.")

    def fetch_fhir_consents(self, sid: str, sid_type: str, consent_template_key: str) -> None:
        """
        Fetch FHIR Consent resources based on the provided parameters.

        :param sid: The signer ID.
        :type sid: str
        :param sid_type: The type of the signer ID.
        :type sid_type: str
        :param consent_template_key: The consent template key.
        :type consent_template_key: str
        """
        logger.debug(f"Fetching FHIR Consent resources from {consent_template_key} for {sid_type} {sid}")
        path: str = f"Consent/?domain:identifier={consent_template_key}" \
                    f"&category=http://fhir.de/ConsentManagement/CodeSystem/ResultType|consent-status&" \
                    f"patient.identifier=https://ths-greifswald.de/fhir/gics/identifiers/{sid_type}|{sid}"
        self.bundler.fetch_resources(path)
