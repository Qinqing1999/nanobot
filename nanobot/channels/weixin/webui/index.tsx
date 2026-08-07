import type { ChannelUiContribution } from "@/channel-plugins/types";
import { chatAppGuideUrl } from "@/components/settings/channels/catalog";

import { WeixinAssistantsPanel } from "./WeixinAssistantsPanel";

export default {
  Panel: WeixinAssistantsPanel,
  canConnectBeforeConfigured: true,
  aliases: {
    wechat: {},
  },
  presentation: {
    displayName: "WeChat",
    initials: "WX",
    color: "#07C160",
    logoUrl: "https://weixin.qq.com/favicon.ico",
    setup: {
      mode: "connect",
      command: "nanobot channels login weixin",
      docsUrl: chatAppGuideUrl("wechat"),
      manualFields: [
        { key: "channels.weixin.allowFrom" },
        { key: "channels.weixin.token" },
      ],
    },
  },
} satisfies ChannelUiContribution;
