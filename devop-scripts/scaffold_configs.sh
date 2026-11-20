#!/bin/bash
GROUP="GoSmart Health"
ROOT="./devop-scripts/examples/"
./configure-cli.sh -c fhir -f "$ROOT/fhir_config.json" -a "$GROUP"

#./configure-cli.sh -c dicom -f "$ROOT/dicom_mdicom_config.json" -a "$GROUP"
#./configure-cli.sh -c dicom -f "$ROOT/dicom_mock_config.json" -a "$GROUP"
#./configure-cli.sh -c llm -f "$ROOT/llm_embedding_config.json" -a "$GROUP"
#./configure-cli.sh -c llm -f "$ROOT/llm_inference_config.json" -a "$GROUP"
#
