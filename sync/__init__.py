#!/usr/bin/env python

import argparse
import logging
from configparser import ConfigParser
from sync.syncer import ConsentSyncer
from sync.bundler import ResourceBundler


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def run():
    parser = argparse.ArgumentParser(description='The FHIR resource bundler searches FHIR resources according to a '
                                                 'given query, resolves all references and bundles them all together.')
    parser.add_argument('-c', '--config', default='config.ini', help='Path to a configuration file.')
    parser.add_argument('-q', '--query', required=True, help='A FHIR search query')
    parser.add_argument('-s', '--sync', action='store_true', help='Sync mode')

    args = parser.parse_args()

    config = ConfigParser()
    config.read(args.config)

    bundler = ResourceBundler(config)

    if args.sync:
        syncer = ConsentSyncer(bundler, config)
    else:
        bundler.fetch_resources(args.query)

if __name__ == '__main__':
    run()
