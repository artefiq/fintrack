import azure.functions as func
from datetime import datetime
from azure.eventgrid import EventGridPublisherClient
from azure.core.credentials import AzureKeyCredential
import os
import json

def main(mytimer: func.TimerRequest) -> None:
    try:
        utc_timestamp = datetime.utcnow().isoformat()

        topic_endpoint = os.getenv("EVENTGRID_TOPIC_ENDPOINT")
        topic_key = os.getenv("EVENTGRID_ACCESS_KEY")

        event_grid_client = EventGridPublisherClient(
            topic_endpoint, AzureKeyCredential(topic_key)
        )

        event = {
            "id": "month-ended-" + utc_timestamp,
            "subject": "Month/Ended",
            "data": {"month": datetime.utcnow().strftime("%Y-%m")},
            "eventType": "Month.Ended",
            "eventTime": utc_timestamp,
            "dataVersion": "1.0"
        }

        event_grid_client.send([event])

        logging.info(f"Month.Ended event sent at {utc_timestamp}")

    except Exception as e:
        logging.error(f"Error in MonthlyReportSchedulerFunction: {e}")
