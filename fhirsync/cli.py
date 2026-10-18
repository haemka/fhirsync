"""The fhirsync command."""
import argparse
import logging
import sys

from fhirsync.bundler import ResourceBundler
from fhirsync.config import ConfigError, load_config

logger = logging.getLogger(__name__)


def run() -> None:
    """
    The fhirsync command: configure logging, read the command line and the configuration, and run the searches of
    a server source (source.searches, or -q instead) or the files of a files source. Exits with code 2 for an
    invalid command line or configuration.
    """
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
                        level=logging.INFO,
                        datefmt='%Y-%m-%d %H:%M:%S')

    parser = argparse.ArgumentParser(description='The FHIR resource bundler reads FHIR resources from a server '
                                                 '(searches) or from files, resolves all references and bundles '
                                                 'them all together.')
    parser.add_argument('-c', '--config', default='config.yaml', help='Path to a YAML configuration file.')
    parser.add_argument('-q', '--query', help='A FHIR search instead of source.searches (source type server only)')
    # Sync mode moved to gics-consent-sync, the option is only kept for a helpful error message
    parser.add_argument('-s', '--sync', action='store_true', help=argparse.SUPPRESS)

    args = parser.parse_args()
    if args.sync:
        parser.error('sync mode moved to gics-consent-sync, see README "Upgrading from fhirsync 0.7 and older"')

    try:
        config = load_config(args.config)
    except ConfigError as e:
        logger.error(str(e))
        sys.exit(2)
    if config.source.type == 'files' and args.query:
        parser.error('-q/--query needs a source of type server')
    if config.source.type == 'server' and not args.query and not config.source.searches:
        parser.error(f'no search: give -q/--query or configure source.searches in {args.config}')

    ResourceBundler(config).run([args.query] if args.query else None)


if __name__ == '__main__':
    run()
