"""The fhirsync command."""
import argparse
import logging
import sys

from fhirsync.bundler import ResourceBundler
from fhirsync.config import ConfigError, load_config

logger = logging.getLogger(__name__)


def run() -> None:
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
                        level=logging.INFO,
                        datefmt='%Y-%m-%d %H:%M:%S')

    parser = argparse.ArgumentParser(description='The FHIR resource bundler searches FHIR resources according to a '
                                                 'given query, resolves all references and bundles them all together.')
    parser.add_argument('-c', '--config', default='config.yaml', help='Path to a YAML configuration file.')
    parser.add_argument('-q', '--query', help='A FHIR search query (required)')
    # Sync mode moved to gics-consent-sync, the option is only kept for a helpful error message
    parser.add_argument('-s', '--sync', action='store_true', help=argparse.SUPPRESS)

    args = parser.parse_args()
    if args.sync:
        parser.error('sync mode moved to gics-consent-sync, see README "Upgrading from fhirsync 0.7 and older"')
    if not args.query:
        parser.error('the following arguments are required: -q/--query')

    try:
        config = load_config(args.config)
    except ConfigError as e:
        logger.error(str(e))
        sys.exit(2)

    ResourceBundler(config).fetch_resources(args.query)


if __name__ == '__main__':
    run()
