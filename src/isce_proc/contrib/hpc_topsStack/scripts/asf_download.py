#!/usr/bin/env python

import os
import asf_search as asf

download = False
csv = True
kml = True

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
wkt = 'POLYGON((49.0748 15.577,62.7941 15.577,62.7941 25.2798,49.0748 25.2798,49.0748 15.577))'


# Search
search_results = asf.geo_search(
    platform        =   asf.PLATFORM.SENTINEL1A,
    intersectsWith  =   wkt,
    start           =   '2014-01-01',
    end             =   '2024-11-15',
    processingLevel =   asf.PRODUCT_TYPE.SLC,
    beamMode        =   asf.BEAMMODE.IW,
    relativeOrbit   =   28,         # Change the path
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
        processes = 12
    )
