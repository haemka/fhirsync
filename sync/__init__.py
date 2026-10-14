#!/usr/bin/env python

import argparse
import logging
from configparser import ConfigParser
from sync.syncer import ConsentSyncer
from sync.bundler import ResourceBundler


logging.basicConfig(format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
                    level=logging.INFO,
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def run():
    parser = argparse.ArgumentParser(description='The FHIR resource bundler searches FHIR resources according to a '
                                                 'given query, resolves all references and bundles them all together.')
    parser.add_argument('-c', '--config', default='config.ini', help='Path to a configuration file.')
    parser.add_argument('-q', '--query', help='A FHIR search query (required unless in sync mode)')
    parser.add_argument('-s', '--sync', action='store_true', help='Sync mode')

    args = parser.parse_args()
    if not args.sync and not args.query:
        parser.error('the following arguments are required: -q/--query (unless -s/--sync is given)')
    if args.sync and args.query:
        logger.warning('Query is ignored in sync mode.')

    config = ConfigParser()
    config.read(args.config)

    bundler = ResourceBundler(config)

    if args.sync:
        ConsentSyncer(bundler, config).run()
    else:
        bundler.fetch_resources(args.query)


if __name__ == '__main__':
    run()
