#!/usr/bin/env python

import os
import asf_search as asf

download = True
csv      = True
kml      = True

# Credential
with open(os.path.expanduser('~/.netrc'), 'r') as f:
    line = f.read().splitlines()[0]
    content = line.split()
    for i, ele in enumerate(content):
        print(content)
        if   ele == 'machine':  server   = content[i+1]
        elif ele == 'login':    username = content[i+1]
        elif ele == 'password': password = content[i+1]
    print(f'machine: {server}  user: {username}')

session = asf.ASFSession().auth_with_creds(username, password)


# SELECT AOI - edit point
# south chile
wkt = 'POLYGON((-75.3782 -36.5364,-65.6094 -36.5364,-65.6094 -22.7645,-75.3782 -22.7645,-75.3782 -36.5364))'
# Peru
#wkt = 'POLYGON((-80.3451 -8.4188,-77.934 -11.8579,-77.5311 -13.5782,-75.4973 -18.1489,-73.8619 -20.6153,-65.0571 -21.0366,-65.8134 -17.0452,-67.7621 -16.2365,-68.8281 -14.5802,-73.0298 -13.12,-74.1679 -12.1135,-76.9443 -8.6473,-77.8208 -7.6986,-80.3451 -8.4188))'
# Peru to Santiago
#wkt = 'POLYGON((-74.0756 -15.913,-73.1385 -16.2766,-72.321 -16.6048,-71.1992 -17.3472,-71.1464 -17.5891,-70.4723 -18.3314,-70.4262 -18.9081,-70.4207 -19.3163,-70.2748 -19.8211,-70.3467 -21.7534,-70.7045 -23.8141,-70.6137 -25.0746,-71.503 -28.744,-71.786 -29.5124,-71.5813 -31.185,-72.5131 -34.2254,-65.4641 -33.5947,-65.7351 -30.3865,-66.2691 -28.9774,-66.1619 -26.8556,-66.1101 -25.305,-65.3528 -23.0913,-65.2373 -21.2217,-64.7248 -19.757,-64.6451 -18.724,-64.8193 -18.254,-65.3324 -17.7416,-66.4328 -17.3316,-67.1596 -17.025,-67.8994 -16.3706,-68.3801 -15.8398,-68.9035 -15.0613,-69.3229 -14.5663,-69.7246 -14.0799,-70.429 -13.7526,-71.2659 -13.5066,-71.9502 -13.1457,-73.1124 -12.5076,-74.6775 -11.2069,-75.9854 -9.9504,-76.913 -9.9414,-78.3725 -10.2731,-77.1377 -12.3176,-76.7915 -12.8389,-76.4319 -14.5609,-74.8683 -15.3138,-74.0756 -15.913))'

#wkt = 'POLYGON((-74.0235 -15.5949,-72.2474 -16.3607,-70.6757 -17.4935,-69.7284 -19.2608,-69.8253 -21.2301,-70.1261 -23.9985,-71.0204 -28.368,-71.3214 -29.6212,-70.2606 -29.8055,-69.6005 -29.6724,-68.9168 -28.9324,-68.1507 -27.0696,-68.1681 -24.9438,-67.3548 -23.1029,-67.2251 -21.2804,-67.5642 -19.5638,-68.7412 -17.5767,-70.0141 -15.8042,-71.9472 -14.5643,-73.1966 -14.4958,-74.0806 -14.8949,-74.0235 -15.5949))'


# Search
search_results = asf.geo_search(
    platform        =   asf.PLATFORM.SENTINEL1A,
    intersectsWith  =   wkt,
    start           =   '2014-01-01',
    end             =   '2025-08-20',
    processingLevel =   asf.PRODUCT_TYPE.SLC,
    beamMode        =   asf.BEAMMODE.IW,
    relativeOrbit   =   120,         # Change the path
    flightDirection =   asf.FLIGHT_DIRECTION.ASCENDING,
)

print(f'--Showing Results--')
for product in search_results:
    print(product.properties['fileName'])


if csv:
    with open("search_results.csv", "w") as f:
        f.writelines(search_results.csv())

if kml:
    with open("search_results.kml", "w") as f:
        f.writelines(search_results.kml())


if download:
    print(f'--Downloading Results--')
    search_results.download(
        path = './',
        session = session,
        processes = 8
    )
