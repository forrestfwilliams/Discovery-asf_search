import warnings
from typing import List
from shapely.geometry import shape, Point, Polygon, mapping
import json

from asf_search import ASFSession, ASFSearchResults
from asf_search.ASFSearchOptions import ASFSearchOptions
from asf_search.download import download_url
# from asf_search.CMR import translate_product
from remotezip import RemoteZip

from asf_search.download.file_download_type import FileDownloadType
from asf_search import ASF_LOGGER
from asf_search.CMR.translate import cast, try_round_float, get_state_vector
from asf_search.CMR.translate import get as umm_get
from asf_search.CMR import umm_property_paths, umm_property_typecasting
# Myabe just these keys????
#start and stop time (maybe)
# - fileID
# - platform
# - geoemetry


class ASFProduct:
    base_properties = {
            # min viable product
            'centerLat',
            'centerLon',
            'fileID', # secondary search results sort key
            'flightDirection',
            'pathNumber',
            'stopTime', # primary search results sort key
            'processingLevel',
            'url'
    }

    def __init__(self, args: dict = {}, session: ASFSession = ASFSession()):
        self.meta = args.get('meta')
        self.umm = args.get('umm')

        translated = self.translate_product(args)

        self.properties = translated['properties']
        self.geometry = translated['geometry']
        self.baseline = None
        self.session = session


    def __str__(self):
        return json.dumps(self.geojson(), indent=2, sort_keys=True)

    def geojson(self) -> dict:
        return {
            'type': 'Feature',
            'geometry': self.geometry,
            'properties': self.properties
        }

    def download(self, path: str, filename: str = None, session: ASFSession = None, fileType = FileDownloadType.DEFAULT_FILE) -> None:
        """
        Downloads this product to the specified path and optional filename.

        :param path: The directory into which this product should be downloaded.
        :param filename: Optional filename to use instead of the original filename of this product.
        :param session: The session to use, defaults to the one used to find the results.

        :return: None
        """

        default_filename = self.properties['fileName']

        if filename is not None:
            multiple_files = (
                (fileType == FileDownloadType.ADDITIONAL_FILES and len(self.properties['additionalUrls']) > 1) 
                or fileType == FileDownloadType.ALL_FILES
            )
            if multiple_files:
                warnings.warn(f"Attempting to download multiple files for product, ignoring user provided filename argument \"{filename}\", using default.")
            else:
                default_filename = filename
                
        if session is None:
            session = self.session

        urls = []

        def get_additional_urls():
            output = []
            base_filename = '.'.join(default_filename.split('.')[:-1])
            for url in self.properties['additionalUrls']:
                extension = url.split('.')[-1]
                urls.append((f"{base_filename}.{extension}", url))
            
            return output

        if fileType == FileDownloadType.DEFAULT_FILE:
            urls.append((default_filename, self.properties['url']))
        elif fileType == FileDownloadType.ADDITIONAL_FILES:
            urls.extend(get_additional_urls())
        elif fileType == FileDownloadType.ALL_FILES:
            urls.append((default_filename, self.properties['url']))
            urls.extend(get_additional_urls())
        else:
            raise ValueError("Invalid FileDownloadType provided, the valid types are 'DEFAULT_FILE', 'ADDITIONAL_FILES', and 'ALL_FILES'")

        for filename, url in urls:
            download_url(url=url, path=path, filename=filename, session=session)

    def stack(
            self,
            opts: ASFSearchOptions = None
    ) -> ASFSearchResults:
        """
        Builds a baseline stack from this product.

        :param opts: An ASFSearchOptions object describing the search parameters to be used. Search parameters specified outside this object will override in event of a conflict.

        :return: ASFSearchResults containing the stack, with the addition of baseline values (temporal, perpendicular) attached to each ASFProduct.
        """
        from .search.baseline_search import stack_from_product

        if opts is None:
            opts = ASFSearchOptions(session=self.session)

        return stack_from_product(self, opts=opts)

    def get_stack_opts(self, opts: ASFSearchOptions=None) -> ASFSearchOptions:
        """
        Build search options that can be used to find an insar stack for this product

        :return: ASFSearchOptions describing appropriate options for building a stack from this product
        """
        from .search.baseline_search import get_stack_opts

        return get_stack_opts(reference=self)

    def centroid(self) -> Point:
        """
        Finds the centroid of a product
        """
        coords = mapping(shape(self.geometry))['coordinates'][0]
        lons = [p[0] for p in coords]
        if max(lons) - min(lons) > 180:
            unwrapped_coords = [a if a[0] > 0 else [a[0] + 360, a[1]] for a in coords]
        else:
            unwrapped_coords = [a for a in coords]

        return Polygon(unwrapped_coords).centroid

    def remotezip(self, session: ASFSession) -> RemoteZip:
        """Returns a RemoteZip object which can be used to download a part of an ASFProduct's zip archive.
        (See example in examples/5-Download.ipynb)
        
        :param session: an authenticated ASFSession
        """
        from .download.download import remotezip

        return remotezip(self.properties['url'], session=session)

    def translate_product(self, item: dict) -> dict:
        try:
            coordinates = item['umm']['SpatialExtent']['HorizontalSpatialDomain']['Geometry']['GPolygons'][0]['Boundary']['Points']
            coordinates = [[c['Longitude'], c['Latitude']] for c in coordinates]
            geometry = {'coordinates': [coordinates], 'type': 'Polygon'}
        except KeyError:
            geometry = {'coordinates': None, 'type': 'Polygon'}

        umm = item.get('umm')

        properties = {
            prop: umm_get(umm, *umm_key_value) for prop, umm_key_value in self._get_property_paths().items()
        }

        for key, cast_type in umm_property_typecasting.items():
            if properties.get(key) is not None:
                properties[key] = cast(cast_type, properties.get(key))
        

        if properties.get('url') is not None:
            properties['fileName'] = properties['url'].split('/')[-1]
        else:
            properties['fileName'] = None

        # Fallbacks
        if properties.get('beamModeType') is None:
            properties['beamModeType'] = umm_get(umm, *umm_property_paths['beamMode'])
        
        if properties.get('platform') is None:
            properties['platform'] = umm_get(umm, *umm_property_paths['platformShortName'])

        # asf_frame_platforms = ['Sentinel-1A', 'Sentinel-1B', 'ALOS', 'SENTINEL-1A', 'SENTINEL-1B']
        # if properties['platform'] in asf_frame_platforms:
        #     properties['frameNumber'] = cast(int, get(umm, 'AdditionalAttributes', ('Name', 'FRAME_NUMBER'), 'Values', 0))
        # else:
        #     properties['frameNumber'] = cast(int, get(umm, 'AdditionalAttributes', ('Name', 'CENTER_ESA_FRAME'), 'Values', 0))

        return {'geometry': geometry, 'properties': properties, 'type': 'Feature'}

    # ASFProduct subclasses define extra/override param key + UMM pathing here 
    @staticmethod
    def _get_property_paths() -> dict:
        return {
                prop: umm_path 
                for prop in ASFProduct.base_properties 
                if (umm_path := umm_property_paths.get(prop)) is not None
            }
    
    def get_baseline_calc_properties(self) -> dict:
        return {}
    
    def get_default_product_type(self):
        # scene_name = product.properties['sceneName']
        
        # if get_platform(scene_name) in ['AL']:
        #     return 'L1.1'
        # if get_platform(scene_name) in ['R1', 'E1', 'E2', 'J1']:
        #     return 'L0'
        # if get_platform(scene_name) in ['S1']:
        #     if product.properties['processingLevel'] == 'BURST':
        #         return 'BURST'
        #     return 'SLC'
        return None
    

    # static helper methods for product type checking
    @staticmethod
    def get_platform(item: dict):
        if (platform := umm_get(item.get('umm'), *umm_property_paths['platform'])) is not None:
            return platform
        
        return umm_get(item.get('umm'), *umm_property_paths['platformShortName'])
    
    @staticmethod
    def get_product_type(item: dict):
        return umm_get(item.get('umm'), *umm_property_paths['processingLevel'])

    
    @staticmethod
    def is_valid_product(item: dict):
        return False
    