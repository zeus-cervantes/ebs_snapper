# -*- coding: utf-8 -*-
# Copyright 2015-2016 Rackspace US, Inc.
"""Module for utility functions."""

from __future__ import print_function
import logging
from datetime import timedelta
import boto3
from pytimeparse.timeparse import timeparse

LOG = logging.getLogger(__name__)


def get_owner_id():
    """Get overall owner account id by finding an AWS instance"""
    LOG.debug('get_owner_id')
    regions = get_regions(must_contain_instances=True)
    for region in regions:
        client = boto3.client('ec2', region_name=region)
        instances = client.describe_instances()
        return list(set([x['OwnerId'] for x in instances['Reservations']]))


def get_regions(must_contain_instances=False):
    """Get regions, optionally filtering by regions containing instances."""
    LOG.debug('get_regions(must_contain_instances=%s)', must_contain_instances)
    client = boto3.client('ec2', region_name='us-east-1')
    regions = client.describe_regions()
    region_names = [x['RegionName'] for x in regions['Regions']]

    if must_contain_instances:
        return [x for x in region_names if region_contains_instances(x)]
    else:
        return region_names


def region_contains_instances(region):
    """Check if a region contains EC2 instances"""
    client = boto3.client('ec2', region_name=region)
    instances = client.describe_instances(
        Filters=[{'Name': 'instance-state-name',
                  'Values': ['running', 'stopped']}]
    )
    return 'Reservations' in instances and len(instances['Reservations']) > 0


def get_topic_arn(topic_name):
    """Search for an SNS topic containing topic_name."""
    regions = get_regions()
    for region in regions:
        client = boto3.client('sns', region_name=region)
        topics = client.list_topics()
        for topic in topics['Topics']:
            splits = topic['TopicArn'].split(':')
            if splits[5] == topic_name:
                return topic['TopicArn']
    raise Exception('Could not find an SNS topic {}'.format(topic_name))


def convert_configurations_to_boto_filter(configuration):
    """Convert JSON settings format to boto3-friendly filter"""
    results = []

    for key, value in configuration.iteritems():
        f = {
            'Name': key,
            'Values': flatten([value])
        }
        results.append(f)

    return results


def sns_publish(TopicArn, Message):
    """Wrapper around SNS client so we can mock and unit test and assert it"""
    sns_client = boto3.client('sns')
    sns_client.publish(TopicArn=TopicArn, Message=Message)


def flatten(l):
    """Flatten, like in ruby"""
    return flatten(l[0]) + (flatten(l[1:]) if len(l) > 1 else []) if type(l) is list else [l]


def parse_snapshot_settings(snapshot_settings):
    """convert JSON snapshot settings to timedeltas"""

    # validate keys are present
    expected_keys = ['retention', 'minimum', 'frequency']
    for k in expected_keys:
        if k not in snapshot_settings['snapshot']:
            raise Exception('missing required snapshot setting {}'.format(k))

    retention_seconds = timeparse(snapshot_settings['snapshot']['retention'])
    retention = timedelta(seconds=retention_seconds)

    frequency_seconds = timeparse(snapshot_settings['snapshot']['frequency'])
    frequency = timedelta(seconds=frequency_seconds)

    return retention, frequency


def get_instance(instance_id, region):
    """find and return the data about a single instance"""
    ec2 = boto3.client('ec2', region_name=region)
    instance_data = ec2.describe_instances(InstanceIds=[instance_id])
    if 'Reservations' not in instance_data:
        raise Exception('Response missing reservations %s', instance_data)

    reservations = instance_data['Reservations']
    instances = sum([[i for i in r['Instances']] for r in reservations], [])
    if not len(instances) == 1:
        raise Exception('Found too many instances for this id %s', instances)

    return instances[0]


def count_snapshots(volume_id, region):
    """count how many snapshots exist for this volume"""
    count = 0

    page_iterator = build_snapshot_paginator(volume_id, region)
    for page in page_iterator:
        count += len(page['Snapshots'])

    return count


def most_recent_snapshot(volume_id, region):
    """find and return the most recent snapshot"""
    recent = {}

    page_iterator = build_snapshot_paginator(volume_id, region)
    for page in page_iterator:
        for s in page['Snapshots']:
            if recent == {} or recent['StartTime'] < s['StartTime']:
                recent = s

    if 'StartTime' in recent:
        return recent

    return None


def get_snapshots_by_volume(volume_id, region):
    """Return snapshots by volume and region"""
    snapshot_list = []

    page_iterator = build_snapshot_paginator(volume_id, region)
    for page in page_iterator:
        for s in page['Snapshots']:
            snapshot_list.append(s)

    return snapshot_list


def build_snapshot_paginator(volume_id, region):
    """Utility function to make pagination of snapshots easier"""
    ec2 = boto3.client('ec2', region_name=region)

    paginator = ec2.get_paginator('describe_snapshots')
    operation_parameters = {'Filters': [
        {'Name': 'volume-id', 'Values': [volume_id]}
    ]}

    return paginator.paginate(**operation_parameters)


def snapshot_and_tag(volume_id, delete_on, region):
    """Create snapshot and retention tag"""

    LOG.info('Creating snapshot in %s of volume %s, valid until %s',
             region, volume_id, delete_on)

    ec2 = boto3.client('ec2', region_name=region)

    snapshot = ec2.create_snapshot(VolumeId=volume_id)

    ec2.create_tags(
        Resources=[snapshot['SnapshotId']],
        Tags=[{'Key': 'DeleteOn', 'Value': delete_on}]
    )


def delete_snapshot(snapshot_id, region):
    """Simple wrapper around deletes so we can mock them"""
    ec2 = boto3.client('ec2', region_name=region)
    ec2.delete_snapshot(SnapshotId=snapshot_id)


def get_volumes(instance_id, region):
    """Get volumes from instance id"""
    instance_details = get_instance(instance_id, region)
    block_devices = instance_details.get('BlockDeviceMappings', [])

    return [bd['Ebs']['VolumeId'] for bd in block_devices]