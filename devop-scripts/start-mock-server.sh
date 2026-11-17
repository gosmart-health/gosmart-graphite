#!/bin/bash
pushd ../../dicom-py-mock-server
./start-fhir.sh &
popd
pushd ../../gosmart-fhir-py-plugin
./start-api.sh &
popd
