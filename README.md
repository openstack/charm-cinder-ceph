# Overview

[Cinder][cinder-upstream] is the OpenStack block storage (volume) service.
[Ceph][ceph-upstream] is a unified, distributed storage system designed for
excellent performance, reliability, and scalability. Ceph-backed Cinder
therefore allows for scalability and redundancy for storage volumes. This
arrangement is intended for large-scale production deployments.

The cinder-ceph charm provides a Ceph (RBD) storage backend for Cinder and is
used in conjunction with the [cinder][cinder-charm] charm and an existing Ceph
cluster (via the [ceph-mon][ceph-mon-charm] or the
[ceph-proxy][ceph-proxy-charm] charms).

Specialised use cases:

* Through the use of multiple application names (e.g. cinder-ceph-1,
  cinder-ceph-2), multiple Ceph clusters can be associated with a single Cinder
  deployment.

* A variety of storage types can be achieved with a single Ceph cluster by
  mapping pools with multiple cinder-ceph applications. For instance, different
  pools could be used for HDD or SSD devices. See option `rbd-pool-name` below.

> **Note**: There is currently no upgrade path to using the cinder-ceph charm
  for older deployments that have the cinder and ceph-mon applications related
  directly. This issue is tracked in bug [LP #1727184][lp-bug-1727184].

# Usage

## Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

#### `pool-type`

The `pool-type` option dictates the storage pool type. See section 'Ceph pool
type' for more information.

#### `rbd-pool-name`

The `rbd-pool-name` option sets an existing rbd pool that Cinder should map
to.

## Ceph pool type

Ceph storage pools can be configured to ensure data resiliency either through
replication or by erasure coding. This charm supports both types via the
`pool-type` configuration option, which can take on the values of 'replicated'
and 'erasure-coded'. The default value is 'replicated'.

For this charm, the pool type will be associated with Cinder volumes.

> **Note**: Erasure-coded pools are supported starting with Ceph Luminous.

### Replicated pools

Replicated pools use a simple replication strategy in which each written object
is copied, in full, to multiple OSDs within the cluster.

The `ceph-osd-replication-count` option sets the replica count for any object
stored within the 'cinder-ceph' rbd pool. Increasing this value increases data
resilience at the cost of consuming more real storage in the Ceph cluster. The
default value is '3'.

> **Important**: The `ceph-osd-replication-count` option must be set prior to
  adding the relation to the ceph-mon (or ceph-proxy) application. Otherwise,
  the pool's configuration will need to be set by interfacing with the cluster
  directly.

### Erasure coded pools

Erasure coded pools use a technique that allows for the same resiliency as
replicated pools, yet reduces the amount of space required. Written data is
split into data chunks and error correction chunks, which are both distributed
throughout the cluster.

> **Note**: Erasure coded pools require more memory and CPU cycles than
  replicated pools do.

When using erasure coded pools for Cinder volumes two pools will be created: a
replicated pool (for storing RBD metadata) and an erasure coded pool (for
storing the data written into the RBD). The `ceph-osd-replication-count`
configuration option only applies to the metadata (replicated) pool.

Erasure coded pools can be configured via options whose names begin with the
`ec-` prefix.

> **Important**: It is strongly recommended to tailor the `ec-profile-k` and
  `ec-profile-m` options to the needs of the given environment. These latter
  options have default values of '1' and '2' respectively, which result in the
  same space requirements as those of a replicated pool.

See [Ceph Erasure Coding][cdg-ceph-erasure-coding] in the [OpenStack Charms
Deployment Guide][cdg] for more information.

## Ceph BlueStore compression

This charm supports [BlueStore inline compression][ceph-bluestore-compression]
for its associated Ceph storage pool(s). The feature is enabled by assigning a
compression mode via the `bluestore-compression-mode` configuration option. The
default behaviour is to disable compression.

The efficiency of compression depends heavily on what type of data is stored
in the pool and the charm provides a set of configuration options to fine tune
the compression behaviour.

> **Note**: BlueStore compression is supported starting with Ceph Mimic.

## Deployment

These instructions will show how to deploy Cinder and connect it to an
existing Juju-managed Ceph cluster.

Let file `cinder.yaml` contain the following:

    cinder:
      block-device: None

Deploy Cinder and add relations to essential OpenStack components:

    juju deploy --config cinder.yaml cinder

    juju add-relation cinder:cinder-volume-service nova-cloud-controller:cinder-volume-service
    juju add-relation cinder:shared-db mysql:shared-db
    juju add-relation cinder:identity-service keystone:identity-service
    juju add-relation cinder:amqp rabbitmq-server:amqp

Now deploy cinder-ceph and add a relation to both the cinder and ceph-mon
applications:

    juju deploy cinder-ceph

    juju add-relation cinder-ceph:storage-backend cinder:storage-backend
    juju add-relation cinder-ceph:ceph ceph-mon:client

Additionally, when both the nova-compute and cinder-ceph applications are
deployed a relation is needed between them:

    juju add-relation cinder-ceph:ceph-access nova-compute:ceph-access

# Documentation

The OpenStack Charms project maintains two documentation guides:

* [OpenStack Charm Guide][cg]: for project information, including development
  and support notes
* [OpenStack Charms Deployment Guide][cdg]: for charm usage information

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cinder-ceph].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[ceph-upstream]: https://ceph.io
[cinder-upstream]: https://docs.openstack.org/cinder
[cinder-charm]: https://jaas.ai/cinder
[ceph-mon-charm]: https://jaas.ai/ceph-mon
[ceph-proxy-charm]: https://jaas.ai/ceph-proxy
[cinder-purestorage-charm]: https://jaas.ai/cinder-purestorage
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-cinder-ceph]: https://bugs.launchpad.net/charm-cinder-ceph/+filebug
[cdg-ceph-erasure-coding]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-erasure-coding.html
[ceph-bluestore-compression]: https://docs.ceph.com/en/latest/rados/configuration/bluestore-config-ref/#inline-compression
[lp-bug-1727184]: https://bugs.launchpad.net/charm-cinder-ceph/+bug/1727184
