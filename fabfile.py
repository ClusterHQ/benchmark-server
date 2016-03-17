# vim: ai ts=4 sts=4 et sw=4 ft=python fdm=indent et foldlevel=0
import os
import time

import boto.ec2
from fabric.api import sudo, task, env, execute, run
from fabric.context_managers import cd, settings, hide
from fabric.contrib import files
from bookshelf.api_v1 import (apt_install,
                              create_docker_group,
                              create_server,
                              destroy as f_destroy,
                              down as f_down,
                              ec2,
                              git_clone,
                              install_docker,
                              is_there_state,
                              load_state_from_disk,
                              log_green,
                              log_red,
                              log_yellow,
                              up as f_up)

from cuisine import (user_ensure,
                     group_ensure,
                     group_user_ensure)

# Modify some global Fabric behaviours:
# Let's disable known_hosts, since on Clouds that behaviour can get in the
# way as we continuosly destroy/create boxes.
env.disable_known_hosts = True
env.use_ssh_config = False
env.eagerly_disconnect = True
env.connection_attemtps = 5
env.user = 'root'


class BenchmarkServerCookbook():
    """
    Collection of helper functions for fabric tasks that are
    used for managing the Benchmarking service.
    """

    def add_user_to_docker_group(self):
        """
        Make sure the ubuntu user is part of the docker group.
        """
        log_green('adding the ubuntu user to the docker group')
        with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                      warn_only=True, capture=True):
            user_ensure('ubuntu', home='/home/ubuntu', shell='/bin/bash')
            group_ensure('docker', gid=55)
            group_user_ensure('docker', 'ubuntu')

    def install_docker(self):
        create_docker_group()
        self.add_user_to_docker_group()
        install_docker()

    def install_docker_compose(self):
        cmd = "curl -L https://github.com/docker/compose/releases/download/1.5.2/docker-compose-`uname -s`-`uname -m` > /usr/local/bin/docker-compose"  # noqa
        sudo(cmd)
        sudo("chmod +x /usr/local/bin/docker-compose")

    def install_packages(self):
        """
        Install required packages.
        """
        sudo('apt-get update')
        apt_install(packages=self.required_packages())
        self.install_docker()
        self.install_docker_compose()

    def required_packages(self):
        """
        :return list: The required packages for this instance.
        """
        return ["git"]

    def start_benchmark_service(self):
        """
        Start the benchmark results service.
        """
        repo = 'benchmark-server'
        git_clone('https://github.com/ClusterHQ/benchmark-server',
                  repo)

        with cd(repo):
            # XXX: Temporary checkout to test this branch
            run("git checkout deploy-benchmark-server-FLOC-3887")
            sudo("docker-compose up")


def get_cloud_config():
    return {
        'ami': 'ami-87bea5b7',
        'distribution': 'ubuntu14.04',
        'username': 'ubuntu',
        'disk_name': '/dev/sda1',
        'disk_size': '40',
        'instance_type': os.getenv('AWS_INSTANCE_TYPE', 't2.micro'),
        'key_pair': os.environ['AWS_KEY_PAIR'],
        'region': os.getenv('AWS_REGION', 'us-west-2'),
        'secret_access_key': os.environ['AWS_SECRET_ACCESS_KEY'],
        'access_key_id': os.environ['AWS_ACCESS_KEY_ID'],
        'security_groups': ['ssh', 'benchmark-service'],
        'instance_name': 'benchmark_service',
        'description': 'Store results from benchmarking runs',
        'key_filename': os.environ['AWS_KEY_FILENAME'],
        'tags': {'name': 'benchmark_service'}
    }

volume_config = {
    'device': '/dev/sdf',
    'mountpoint': '/data/volumes',
    'size': 20,
}


@task(default=True)
def help():
    """
    Print the help text.
    """

    help_text = (
        """
        Start an AWS instance that is running the Benchmarking results
        service.

        usage: fab <action>

        # Start the service
        $ fab start

        # Provision and start the AWS instance if it does not exist,
        # otherwise start an existing instance.
        $ fab up

        # Suspend the instance.
        $ fab down

        # Destroy the instance.
        $ fab destroy
        """
    )
    print help_text


@task
def destroy():
    """
    Destroy an existing instance.
    """
    cloud_config = get_cloud_config()
    if is_there_state():
        data = load_state_from_disk()
        cloud_type = data['cloud_type']
        region = data['region']
        access_key_id = cloud_config['access_key_id']
        secret_access_key = cloud_config['secret_access_key']
        instance_id = data['id']
        env.user = data['username']
        env.key_filename = cloud_config['key_filename']

        f_destroy(cloud=cloud_type,
                  region=region,
                  instance_id=instance_id,
                  access_key_id=access_key_id,
                  secret_access_key=secret_access_key)


@task
def down():
    """
    Halt an existing instance.
    """
    cloud_config = get_cloud_config()
    if is_there_state():
        data = load_state_from_disk()
        region = data['region']
        cloud_type = data['cloud_type']
        access_key_id = cloud_config['access_key_id']
        secret_access_key = cloud_config['secret_access_key']
        instance_id = data['id']
        env.key_filename = cloud_config['key_filename']

        ec2()
        f_down(cloud=cloud_type,
               instance_id=instance_id,
               region=region,
               access_key_id=access_key_id,
               secret_access_key=secret_access_key)


@task
def up():
    """
    Boots a new Ubuntu instance on AWS, or start the existing instance.
    """
    cloud_config = get_cloud_config()
    if is_there_state():
        data = load_state_from_disk()
        cloud_type = data['cloud_type']
        username = data['username']
        region = data['region']
        access_key_id = cloud_config['access_key_id']
        secret_access_key = cloud_config['secret_access_key']
        instance_id = data['id']
        env.user = data['username']
        env.key_filename = cloud_config['key_filename']

        ec2()

        f_up(cloud=cloud_type,
             region=region,
             instance_id=instance_id,
             access_key_id=access_key_id,
             secret_access_key=secret_access_key,
             username=username)
    else:
        env.user = cloud_config['username']
        env.key_filename = cloud_config['key_filename']

        # No state file exists so create a new VM and use the default
        # values from the 'cloud_config' dictionary
        create_server(cloud='ec2',
                      region=cloud_config['region'],
                      access_key_id=cloud_config['access_key_id'],
                      secret_access_key=cloud_config['secret_access_key'],
                      distribution=cloud_config['distribution'],
                      disk_name=cloud_config['disk_name'],
                      disk_size=cloud_config['disk_size'],
                      ami=cloud_config['ami'],
                      key_pair=cloud_config['key_pair'],
                      instance_type=cloud_config['instance_type'],
                      instance_name=cloud_config['instance_name'],
                      username=cloud_config['username'],
                      security_groups=cloud_config['security_groups'],
                      tags=cloud_config['tags'])
        if is_there_state():
            data = load_state_from_disk()
            env.hosts = data['ip_address']
            env.cloud = data['cloud_type']


def volume_metadata_exists():
    return os.path.isfile('volume-metadata.json')


@task
def configure_storage():
    cloud_config = get_cloud_config()
    if is_there_state():
        data = load_state_from_disk()
        instance_id = data['id']
        conn = boto.ec2.connect_to_region(
            cloud_config['region'],
            aws_access_key_id=cloud_config['access_key_id'],
            aws_secret_access_key=cloud_config['secret_access_key']
        )

        [instance] = conn.get_only_instances(instance_id)

        if volume_metadata_exists():
            log_green('Volume exists.')
            # At this point, we need to read the volume metadata from
            # the local disk and use it for the remaining steps.
        else:
            # Create a volume and wait for it to become available.
            vol = conn.create_volume(volume_config['size'], instance.placement)
            vol.add_tag("Name", "benchmarking-data-mongodb")
            while vol.update() != 'available':
                log_yellow(
                    "Waiting on volume {id} to become available. "
                    "Current status: {status}".format(
                        id=vol.id, status=vol.status
                    )
                )
                time.sleep(5)

        requested_device = volume_config['device']

        if vol.update() == 'in-use':
            if vol.attach_data.instance_id != instance_id:
                log_red(
                    "Error: {volume} is attached to wrong instance: "
                    "{instance}".format(
                        volume=vol.id,
                        instance=vol.attach_data.instance
                    )
                )
                # TODO: Raise an error here
        elif vol.update() == 'available':
            # Attach the volume and wait for it to be in use.
            # TODO: Address the issue where the volume might be in a
            # different availability zone.
            conn.attach_volume(vol.id, instance_id, requested_device)
            while vol.update() != 'in-use':
                log_yellow(
                    "Waiting on volume {id} to attach. "
                    "Current status: {status}".format(
                        id=vol.id, status=vol.status
                    )
                )
                time.sleep(5)

        def convert_device_path(device_path):
            prefix = b"/dev/sd"
            return os.path.join("/dev", "xvd" + device_path[len(prefix):])

        device_path = convert_device_path(requested_device)

        # Wait for device to be available within the instance.
        while not files.exists(device_path):
            log_yellow(
                "Waiting on {device_path}".format(device_path=device_path)
            )
            time.sleep(5)

        def device_has_filesystem(device_path):
            cmd = "blkid -p -u filesystem {device}".format(device=device_path)
            with settings(warn_only=True):
                output = sudo(cmd)
                if output.return_code == 2:
                    return False
                return True

        # Create a filesystem if one doesn't exist
        if not device_has_filesystem(device_path):
            cmd = "mkfs -t ext4 {device}".format(device=device_path)
            sudo(cmd)

        mountpoint = volume_config['mountpoint']
        sudo("mkdir -p {path}".format(path=mountpoint))
        sudo(
            "mount {device_path} {mountpoint}".format(
                device_path=device_path, mountpoint=mountpoint
            )
        )
        # TODO: Store the volume metadata locally so that it can be
        # retrieved later.


@task
def start():
    execute(up)
    execute(configure_storage)
    execute(bootstrap)


@task
def bootstrap():
    cookbook = BenchmarkServerCookbook()

    cookbook.install_packages()
    cookbook.start_benchmark_service()
