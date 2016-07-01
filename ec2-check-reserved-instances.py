#!/usr/bin/python
'''
-i ignoring things from output:
{
    "reserved": [
        { "type": "hi1.4xlarge", "zone": "us-east-1b", "count": 1 },
        { "type": "hi1.4xlarge", "zone": "us-east-1a", "count": 1 },
    ],
    "running": [
        { "type": "t2.micro", "zone": "us-east-1e", "count": 5 }
    ]
}
'''

import argparse
import json
import os

import boto3
import ConfigParser

# parse arguments
parser = argparse.ArgumentParser()
parser.add_argument("-v", "--vpc_sensitive", dest="vpc_sensitive", action='store_true', default=False,
                    help="Add this flag if you wish to see VPC-related discrepancies")
parser.add_argument("-r", "--region", dest="region_name", help="AWS region", required=True)
parser.add_argument("-c", "--config_file", dest="config_file", help="Config file containing credentials",
                    required=False)
parser.add_argument("-i", "--ignore_file", dest="ignore_file", help="Config file containing instance types to ignore",
                    required=False)

options = parser.parse_args()

AWS_ACCESS_KEY_ID=os.environ.get('AWSAccessKeyId')
AWS_SECRET_ACCESS_KEY=os.environ.get('AWSSecretKey')

# load config
filename = options.config_file if options.config_file else os.path.expanduser("~/.aws/credentials")
if os.path.exists(filename):
    config = ConfigParser.ConfigParser()
    config.read(filename)
    AWS_ACCESS_KEY_ID = config.get('default', 'aws_access_key_id')
    AWS_SECRET_ACCESS_KEY = config.get('default', 'aws_secret_access_key')

ignore = None
if options.ignore_file:
    with open(options.ignore_file) as data_file:
        ignore = json.load(data_file)

if AWS_ACCESS_KEY_ID == None and AWS_SECRET_ACCESS_KEY == None:
    print "Please set env variables (AWSAccessKeyId, AWSSecretKey), or populate ~/.aws/config (official aws client)"
    sys.exit(1)


# connect to ec2
print(">>> Connecting to EC2...")
ec2_conn = boto3.client('ec2', region_name=options.region_name,
                        aws_access_key_id=AWS_ACCESS_KEY_ID,
                        aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
debug = False

reservations = ec2_conn.describe_instances()
running_instances = {}
for reservation in reservations['Reservations']:
    for instance in reservation['Instances']:
        if debug:
            if instance['State']['Name'] != "running":
                print("Disqualifying instance %s: not running" % (instance['InstanceId']))
            elif 'SpotInstanceRequestId' in instance:
                print("Disqualifying instance %s: spot" % (instance['InstanceId']))
        if instance['State']['Name'] == "running" and not 'SpotInstanceRequestId' in instance:
            az = instance['Placement']['AvailabilityZone']
            if 'VpcId' in instance and options.vpc_sensitive:
                az = az + '_vpc'
            if 'Platform' in instance:
                az = az + '_windows'
            instance_type = instance['InstanceType']
            running_instances[(instance_type, az)] = running_instances.get((instance_type, az), 0) + 1

if ignore and ignore['running']:
    for ignore_item in ignore['running']:
        if (ignore_item['type'], ignore_item['zone']) in running_instances:
            running_instances[(ignore_item['type'], ignore_item['zone'])] = \
                running_instances[(ignore_item['type'], ignore_item['zone'])] - ignore_item['count']

reserved_instances = {}
reserved_list = ec2_conn.describe_reserved_instances()
for reserved_instance in reserved_list['ReservedInstances']:
    if debug and reserved_instance['State'] != "active":
        print("Excluding reserved instances %s: no longer active" % (reserved_instance['ReservedInstancesId']))
    if reserved_instance['State'] == "active":
        az = reserved_instance['AvailabilityZone']
        if 'VPC' in reserved_instance['ProductDescription'] and options.vpc_sensitive:
            az = az + '_vpc'
        if 'Windows' in reserved_instance['ProductDescription']:
            az = az + '_windows'
        instance_type = reserved_instance['InstanceType']
        reserved_instances[(instance_type, az)] = reserved_instances.get((instance_type, az), 0) + \
                                                  reserved_instance['InstanceCount']

if ignore and ignore['reserved']:
    for ignore_item in ignore['reserved']:
        if (ignore_item['type'], ignore_item['zone']) in reserved_instances:
            reserved_instances[(ignore_item['type'], ignore_item['zone'])] = \
                reserved_instances[(ignore_item['type'], ignore_item['zone'])] - ignore_item['count']

# this dict will have a positive number if there are unused reservations
# and negative number if an instance is on demand
instance_diff = dict([(x, reserved_instances[x] - running_instances.get(x, 0)) for x in reserved_instances])

# instance_diff only has the keys that were present in reserved_instances. There's probably a cooler way to add a filtered dict here
for placement_key in running_instances:
    if not placement_key in reserved_instances:
        instance_diff[placement_key] = -running_instances[placement_key]

unused_reservations = dict((key, value) for key, value in instance_diff.iteritems() if value > 0)
if unused_reservations == {}:
    print("Congratulations, you have no unused reservations")
else:
    for unused_reservation in unused_reservations:
        print("UNUSED RESERVATION!\t(%s)\t%s\t%s" % (
            unused_reservations[unused_reservation], unused_reservation[0], unused_reservation[1]))

print("")

unreserved_instances = dict((key, -value) for key, value in instance_diff.iteritems() if value < 0)
if unreserved_instances == {}:
    print("Congratulations, you have no unreserved instances")
else:
    for unreserved_instance in unreserved_instances:
        print("Instance not reserved:\t(%s)\t%s\t%s" % (
            unreserved_instances[unreserved_instance], unreserved_instance[0], unreserved_instance[1]))

qty_running_instances = reduce(lambda x, y: x + y, running_instances.values())
qty_reserved_instances = reduce(lambda x, y: x + y, reserved_instances.values())

print("\n(%s) running on-demand instances\n(%s) reservations" % (qty_running_instances, qty_reserved_instances))
