#!/usr/bin/env python2.7
from __future__ import division

import boto3
import json
import time
import os

queue_url   = os.environ['SQS_QUEUE_URL']
ddb_table   = os.environ['DDB_TABLE']
ecs_cluster = os.environ['ECS_CLUSTER']
aws_region  = os.environ['AWS_REGION']

boto3.setup_default_session(region_name=aws_region)

sqs   = boto3.client('sqs')
cw    = boto3.client('cloudwatch')
ddb   = boto3.client('dynamodb')
ecs   = boto3.client('ecs')

def parse_alarm_descr(alarm_descr):
    alarm_descr_dict = None
    
    try:
        alarm_descr_dict = dict()
        alarm_descr_dict['ecs_service']   = alarm_descr.split(',')[0]
        alarm_descr_dict['min_tasks']     = int(alarm_descr.split(',')[1])
        alarm_descr_dict['max_tasks']     = int(alarm_descr.split(',')[2])
        alarm_descr_dict['cooldown']      = int(alarm_descr.split(',')[3])
        alarm_descr_dict['scale_percent'] = int(alarm_descr.split(',')[4])
    except Exception as e:
        print "Caught exception while parsing desciption: ", e
    
    return alarm_descr_dict

def is_alarm_alarming(alarm_name):
    desc_alarms_result = cw.describe_alarms(AlarmNames=[alarm_name])
    alarm = desc_alarms_result['MetricAlarms'][0]
    return alarm['StateValue'] == 'ALARM'

def delete_message(message):
    message_id     = message['MessageId']
    receipt_handle = message['ReceiptHandle']
    print "Deleting message id=%s" % message_id
    
    sqs.delete_message(
        QueueUrl = queue_url,
        ReceiptHandle = receipt_handle
    )

def get_desired_task_count(service_name):
    desc_svc_result = ecs.describe_services(
        cluster = ecs_cluster,
        services = [
            service_name
        ]
    )
    
    service = desc_svc_result['services'][0]
    return service['desiredCount']

def get_last_scaling_activity(alarm_name):
    ddb_response = ddb.get_item(
        TableName=ddb_table,
        Key = {
            'AlarmId' : {
                'S' : alarm_name
            }
        }
    )
    
    item = ddb_response.get('Item')
    if item:
        return float(item['LastScalingActivity']['N'])
    else:
        return None

def update_last_scaling_activity(alarm_name):
    ddb_response = ddb.put_item(
        TableName=ddb_table,
        Item = {
            'AlarmId' : {
                'S' : alarm_name
            },
            'LastScalingActivity' : {
                'N' : "%s" % time.time()
            }
        }
    )

def get_new_desired_taskcount(alarm_descr_dict):
    
    current_desired_task_count = get_desired_task_count(alarm_descr_dict['ecs_service'])
    print "Current desired task count: %d" % current_desired_task_count
    
    scale_percent = alarm_descr_dict['scale_percent']
    min_tasks     = alarm_descr_dict['min_tasks']
    max_tasks     = alarm_descr_dict['max_tasks']
    cooldown      = alarm_descr_dict['cooldown']
    
    desired_taskcount_increment = current_desired_task_count * (scale_percent/100)
    
    if desired_taskcount_increment > 0 and desired_taskcount_increment < 1:
        desired_taskcount_increment = 1
    elif desired_taskcount_increment <0 and desired_taskcount_increment > -1:
        desired_taskcount_increment = -1
    
    if desired_taskcount_increment > 0 and desired_taskcount_increment < 1:
        desired_taskcount_increment = 1
    elif desired_taskcount_increment <0 and desired_taskcount_increment > -1:
        desired_taskcount_increment = -1
        
    new_task_count = current_desired_task_count + desired_taskcount_increment
    
    if new_task_count > max_tasks:
        new_task_count = max_tasks
    
    if new_task_count < min_tasks:
        new_task_count = min_tasks
    
    return int(new_task_count)

def can_scale(alarm_descr_dict, alarm_name):
    
    min_tasks     = alarm_descr_dict['min_tasks']
    max_tasks     = alarm_descr_dict['max_tasks']
    cooldown      = alarm_descr_dict['cooldown']
    
    current_desired_task_count = get_desired_task_count(alarm_descr_dict['ecs_service'])
    new_task_count = get_new_desired_taskcount(alarm_descr_dict)
    
    if new_task_count < min_tasks or new_task_count > max_tasks:
        print "Unable to scale: Not %d < (new task count: %d) < %d" % (min_tasks, new_task_count, max_tasks)
        return False
    
    if new_task_count == current_desired_task_count:
        print "Unable to scale since new task count == current desired task count"
        return False
    
    last_scaling_activity = get_last_scaling_activity(alarm_name)
    
    if last_scaling_activity is None:
        return True
    
    # If last scaling activity + cooldown is before now then return true
    current_time = time.time()
    if last_scaling_activity + cooldown < current_time:
        return True
    else:
        print "Can't scale now because cooldown period has not passed"
    
    return False

def scale(service_name, new_desired_task_count):
    
    print "Setting desired task count to %d for %s" % (new_desired_task_count, service_name)
    
    update_svc_result = ecs.update_service(
        cluster = ecs_cluster,
        service = service_name,
        desiredCount = new_desired_task_count
    )
        
    print "Update Service status: %s" % update_svc_result['ResponseMetadata']
    
    

def handle_message(message):
    sns_body = json.loads(message['Body'].encode("ascii"))
    cw_alarm_notif = json.loads(sns_body['Message'])

    alarm_name  = cw_alarm_notif.get('AlarmName')
    alarm_descr = cw_alarm_notif.get('AlarmDescription')
    
    print "------------------------------------------------------"
    print "Received CW alarm %s" % alarm_name
    
    alarm_descr_dict = parse_alarm_descr(alarm_descr)
    if not alarm_descr_dict:
        print "Alarm description %s is not valid" % alarm_descr
        delete_message(message)
        return
    
    if not is_alarm_alarming(alarm_name):
        print "Alarm %s is no longer alarming" % alarm_name
        delete_message(message)
        return
    else:
        print "Alarm %s is still alarming" % alarm_name
    
    service_name = alarm_descr_dict.get('ecs_service')
    
    if can_scale(alarm_descr_dict, alarm_name):
        new_desired_task_count = get_new_desired_taskcount(alarm_descr_dict)
        scale(service_name, new_desired_task_count)
        update_last_scaling_activity(alarm_name)
    else:
        print "Skipping scaling activity"
    

def main():
    
    while(True):
        print "--- Polling for messages ---"
        messages = sqs.receive_message(
                                    QueueUrl            = queue_url,
                                    MaxNumberOfMessages = 1,
                                    WaitTimeSeconds     = 20)
    
        if messages and messages.get('Messages'):
            handle_message(messages['Messages'][0])

if __name__ == "__main__":
    main()
