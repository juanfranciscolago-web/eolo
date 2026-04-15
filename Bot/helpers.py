from loguru import logger
import json
from google.cloud import secretmanager, firestore

def retrieve_google_secret_dict(
    gcp_id, secret_id, version_id="latest"
) -> dict:
    client = secretmanager.SecretManagerServiceClient()

    name = f"projects/{gcp_id}/secrets/{secret_id}/versions/{version_id}"

    secret_response = client.access_secret_version(request={"name": name})

    secret_string = secret_response.payload.data.decode("UTF-8")

    secret_dict = json.loads(secret_string)

    logger.debug(f"Retrieved {version_id} secret value for {secret_id}.")

    return secret_dict

def retrieve_firestore_value(collection_id, document_id, key) -> str:
    db = firestore.Client()

    try:
        document = db.collection(collection_id).document(document_id)

        doc = document.get()

        if doc.exists:
            logger.debug(f"Successfully retrieved {key} value.")
            return doc.get(key)

        else:
            logger.error(f"Failed to retrieve {key} value")

    except Exception as e:

        logger.error(f"Failed to retrieve {key} value")

        return None

def store_firestore_value(project_id, collection_id, document_id, value):
    db = firestore.Client(project=project_id)

    collection = db.collection(collection_id)

    document = collection.document(document_id)

    document.set(value)

    logger.debug(f"Updated {document_id} value.")
