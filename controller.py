import boto3
import time
import os
from dotenv import load_dotenv
from enum import Enum
import math

# Load all environment variables
load_dotenv()
AMI_ID = os.getenv("app_tier_ami_id")
INSTANCE_TYPE = os.getenv("app_tier_instance_type")
KEY_PAIR = os.getenv("key_pair")
SECURITY_GROUP_ID = os.getenv("security_group_id")
SUBNET_ID = os.getenv("subnet_id")
REGION = os.getenv("region_name")
aws_access_key_id = os.getenv("aws_access_key_id")
aws_secret_access_key = os.getenv("aws_secret_access_key")
region_name = os.getenv("region_name")

input_queue_url = os.getenv("input_queue_url")
output_queue_url = os.getenv("output_queue_url")


class Instance_State(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SHUTTING_DOWN = "shutting-down"
    TERMINATED = "terminated"
    STOPPING = "stopping"
    STOPPED = "stopped"


class EC2:
    def __init__(self) -> None:
        self.ec2_client = boto3.client(
            "ec2",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )

        self.instance_IDs = []

    def launch_instances(self, min_count, max_count):
        responses = self.ec2_client.run_instances(
            ImageId=AMI_ID,
            InstanceType=INSTANCE_TYPE,
            KeyName=KEY_PAIR,
            SecurityGroupIds=[SECURITY_GROUP_ID],
            SubnetId=SUBNET_ID,
            MinCount=min_count,
            MaxCount=max_count,
        )

        # Extract instance IDs
        instance_ids = [instance["InstanceId"] for instance in responses["Instances"]]

        # Create tag names dynamically
        tags = [
            {
                "ResourceId": instance_id,
                "Tags": [{"Key": "Name", "Value": f"app-tier-instance-{i+1}"}],
            }
            for i, instance_id in enumerate(instance_ids)
        ]

        # Apply the tags
        self.ec2_client.create_tags(
            Resources=[tag["ResourceId"] for tag in tags],
            Tags=[{"Key": "Name", "Value": tag["Tags"][0]["Value"]} for tag in tags],
        )

        return instance_ids

    def terminate_instances(self, instance_IDs=None):
        if instance_IDs:
            instance_IDs = [i for i in instance_IDs if i != "i-0dd39ee74722684db"]
        self.ec2_client.terminate_instances(
            InstanceIds=instance_IDs if instance_IDs else self.instance_IDs
        )

    def stop_instances(self, instance_IDs=None):
        self.ec2_client.stop_instances(
            InstanceIds=instance_IDs if instance_IDs else self.instance_IDs
        )

    def get_instances_by_state(self, instance_state: Instance_State):
        response = self.ec2_client.describe_instances(
            Filters=[
                {"Name": "instance-state-name", "Values": [instance_state.value]},
                {"Name": "image-id", "Values": [AMI_ID]},
            ]
        )
        instance_IDs = []
        for items in response["Reservations"]:
            for instance in items["Instances"]:
                instance_IDs.append(instance["InstanceId"])

        return instance_IDs

    def start_instances(self, instance_IDs=None):
        if instance_IDs is None:
            instance_IDs = self.get_stopped_instances()  # Get all stopped instances

        if instance_IDs:
            self.ec2_client.start_instances(InstanceIds=instance_IDs)
            return f"Starting instances: {instance_IDs}"
        else:
            return "No instances to start."


class SQS:
    def __init__(self) -> None:
        self.sqs_client = boto3.client(
            "sqs",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )

    def get_queue_length(self, queue_url):
        queue_length = int(
            self.sqs_client.get_queue_attributes(
                QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages"]
            )["Attributes"]["ApproximateNumberOfMessages"]
        )

        return queue_length

    def clear_queue(self, queue_url):
        self.sqs_client.purge_queue(QueueUrl=queue_url)


# Get required number of instances at any given time
def required_instaces(queue_len: int):
    MAX = 15
    return min(MAX, math.ceil(queue_len / 6))


def maintain_state(required_num_instances, ec2: EC2):
    # WAIT_TIME = 60

    running_instances = ec2.get_instances_by_state(Instance_State.RUNNING)
    pending_instances = ec2.get_instances_by_state(Instance_State.PENDING)

    # if pending_instances:
    #     time.sleep(WAIT_TIME)

    stopped_instances = ec2.get_instances_by_state(Instance_State.STOPPED)
    # stopping_instances = ec2.get_instances_by_state(Instance_State.STOPPING)

    total_active_instances = len(running_instances) + len(pending_instances)
    
    if required_num_instances > total_active_instances:
        to_start = required_num_instances - total_active_instances
        print(f"Starting {to_start} instacnes.")
        ec2.start_instances(stopped_instances[:to_start])

    elif required_num_instances < len(running_instances):
        to_stop = len(running_instances) - required_num_instances
        print(f"Stopping {to_stop} instances.")
        ec2.stop_instances(running_instances[:to_stop])


if __name__ == "__main__":
    ec2 = EC2()
    sqs = SQS()

    try:
        while True:

            queue_len = 0
            for i in range(5):
                queue_len += sqs.get_queue_length(input_queue_url)
                
            queue_len = queue_len // 5
            
            

            required_num_instances = required_instaces(queue_len)

            print(
                f"Input Queue Length: {queue_len}. Instances reequired: {required_num_instances}"
            )

            maintain_state(required_num_instances, ec2)

            time.sleep(1)
    except KeyboardInterrupt as keyboard_interrupt:
        print("keyboard Interrupt, stopping now")
