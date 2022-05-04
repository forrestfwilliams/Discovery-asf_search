from asf_search.search.search import ASFProduct, ASFSearchResults
from unittest.mock import patch

def run_test_ASFProduct_Geo_Search(geographic_response):
    product = ASFProduct(geographic_response)

    geojson = product.geojson()
    assert(geojson['geometry'] == geographic_response['geometry'])
    assert(geojson['properties'] == geographic_response['properties'])

def run_test_stack(reference, pre_processed_stack, processed_stack):
    product = ASFProduct(reference)
    
    with patch('asf_search.baseline_search.search') as search_mock:
        search_mock.return_value = ASFSearchResults(map(ASFProduct, pre_processed_stack))
        stack = product.stack()
        
        for(idx, secondary) in enumerate(stack):
            if(idx > 0):
                assert(secondary.properties['temporalBaseline'] >= stack[idx - 1].properties['temporalBaseline'])
            assert(secondary.properties['temporalBaseline'] != None)
            assert(secondary.properties['perpendicularBaseline'] != None)
            assert(secondary.properties['temporalBaseline'] == processed_stack[idx]['properties']['temporalBaseline'])
            assert(secondary.properties['perpendicularBaseline'] == processed_stack[idx]['properties']['perpendicularBaseline'])