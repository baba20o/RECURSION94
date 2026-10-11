        org     $000000

        include "game/engine_layout.asm"
        include "assets/generated/content/campaign_content.inc.asm"
        include "assets/generated/campaign_runtime/campaign_runtime.inc.asm"

CampaignRuntimeStandaloneEnd:
        assert  CAMPAIGN_RUNTIME_FORMAT_VERSION=$0003,"runtime adapter format mismatch"
        assert  CampaignRuntimeDescriptorTableEnd-CampaignRuntimeDescriptorTable=CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"runtime descriptor table size mismatch"
        assert  CampaignRuntimeProfileTableEnd-CampaignRuntimeProfileTable=CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"runtime profile table size mismatch"
        assert  CampaignRuntimeObjectMetadataTableEnd-CampaignRuntimeObjectMetadataTable=CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"runtime metadata table size mismatch"
        assert  CampaignRuntimeStandaloneEnd-ContentCampaignBinaryEnd>0,"runtime adapter was not assembled"
