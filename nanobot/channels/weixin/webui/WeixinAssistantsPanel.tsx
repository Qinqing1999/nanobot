import { useState } from "react";
import { Loader2, RotateCcw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  channelTranslator,
  type ChannelTranslator,
} from "@/channel-plugins/i18n";
import type { ChannelPluginPanelProps } from "@/channel-plugins/types";
import { ChannelInstancesPanel } from "@/components/settings/channels/ChannelInstancesPanel";
import { Button } from "@/components/ui/button";
import { deleteChannelInstance, disableNanobotFeature, enableNanobotFeature, disconnectChannelConnect } from "@/lib/api";
import type {
  NanobotChannelInstanceInfo,
  NanobotFeatureInfo,
  NanobotFeaturesPayload,
} from "@/lib/types";

import { WeixinConnectFlow } from "./WeixinConnectFlow";

export function WeixinAssistantsPanel({
  token,
  feature,
  showBrandLogos,
  chatAppsDocsUrl,
  onFeaturesUpdate,
}: ChannelPluginPanelProps) {
  const { t } = useTranslation();
  const tx = channelTranslator(t, "weixin");
  const instances = feature.instances?.length
    ? feature.instances
    : [defaultWeixinInstance(feature)];

  return (
    <ChannelInstancesPanel
      token={token}
      feature={feature}
      showBrandLogos={showBrandLogos}
      chatAppsDocsUrl={chatAppsDocsUrl}
      instances={instances}
      onFeaturesUpdate={onFeaturesUpdate}
      customization={{
        countLabel: (count) => weixinAccountCountLabel(count, tx),
        toggleAriaLabel: (instance) => tx("custom.toggleAccount", "{{name}} account", {
          name: instanceDisplayName(instance),
        }),
        configuredLabel: tx("custom.configured", "Connected"),
        needsSetupLabel: tx("custom.needsSetup", "Needs login"),
        renderInstanceSummary: (instance) => {
          const name = instance.display_name?.trim() || instance.name.trim();
          if (instance.configured) {
            return tx("custom.connectedAs", "Connected as {{name}}", { name });
          }
          return tx("custom.notConnected", "Not connected");
        },
        renderInstanceAction: (instance) => (
          <WeixinInstanceAction
            key={instance.id}
            token={token}
            instance={instance}
            onFeaturesUpdate={onFeaturesUpdate}
          />
        ),
        footer: (
          <div className="mt-4 overflow-hidden rounded-[16px] border border-border/70 bg-background px-4 py-4">
            <div className="text-[13px] font-semibold text-foreground">
              {tx("custom.createAnother", "Connect another WeChat account")}
            </div>
            <p className="mt-1 text-[12.5px] leading-5 text-muted-foreground">
              {tx(
                "custom.createHint",
                "Connect a separate WeChat account for another team or workflow.",
              )}
            </p>
            <WeixinConnectFlow
              token={token}
              instanceId="default"
              mode="create"
              idleLabel={tx("custom.createAccount", "Connect account")}
              onFeaturesUpdate={onFeaturesUpdate}
            />
          </div>
        ),
      }}
    />
  );
}

function WeixinInstanceAction({
  token,
  instance,
  onFeaturesUpdate,
}: {
  token: string;
  instance: NanobotChannelInstanceInfo;
  onFeaturesUpdate: (payload: NanobotFeaturesPayload) => void;
}) {
  const { t } = useTranslation();
  const tx = channelTranslator(t, "weixin");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!instance.configured) {
    return (
      <WeixinConnectFlow
        token={token}
        instanceId={instance.id}
        mode="replace"
        idleLabel={t("settings.channels.connect", { defaultValue: "Connect" })}
        onFeaturesUpdate={onFeaturesUpdate}
      />
    );
  }

  const reconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      onFeaturesUpdate(
        await enableNanobotFeature(token, "weixin", { instanceId: instance.id }),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      await disconnectChannelConnect(token, "weixin", { instanceId: instance.id });
      onFeaturesUpdate(
        await disableNanobotFeature(token, "weixin", { instanceId: instance.id }),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const deleteInstance = async () => {
    setBusy(true);
    setError(null);
    try {
      await deleteChannelInstance(token, "weixin", { instanceId: instance.id });
      onFeaturesUpdate(
        await disableNanobotFeature(token, "weixin", { instanceId: instance.id }),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const isDefault = instance.id === "default";

  return (
    <>
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 rounded-full border-border/65 bg-background/80 px-3 text-[12px] font-semibold hover:bg-muted/70"
          onClick={() => void reconnect()}
          disabled={busy || !instance.enabled}
        >
          {busy ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          )}
          {tx("custom.reconnect", "Reconnect")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 rounded-full border-destructive/30 bg-background/80 px-3 text-[12px] font-semibold text-destructive hover:bg-destructive/10"
          onClick={() => void disconnect()}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          )}
          {tx("custom.disconnect", "Disconnect")}
        </Button>
        {!isDefault ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 rounded-full border-destructive/30 bg-background/80 px-3 text-[12px] font-semibold text-destructive hover:bg-destructive/10"
            onClick={() => {
              if (window.confirm(tx("custom.confirmDelete", "Delete this WeChat instance? This cannot be undone."))) {
                void deleteInstance();
              }
            }}
            disabled={busy}
          >
            {busy ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {tx("custom.delete", "Delete")}
          </Button>
        ) : null}
      </div>
      {error ? (
        <div className="mt-3 rounded-[12px] border border-destructive/20 px-3 py-2 text-[12px] leading-5 text-destructive">
          {error}
        </div>
      ) : null}
    </>
  );
}

function defaultWeixinInstance(feature: NanobotFeatureInfo): NanobotChannelInstanceInfo {
  return {
    id: "default",
    name: "nanobot",
    enabled: feature.enabled,
    configured: Boolean(feature.configured),
    config_values: feature.config_values ?? {},
    configured_fields: feature.configured_fields ?? [],
  };
}

function weixinAccountCountLabel(
  count: number,
  tx: ChannelTranslator,
): string {
  if (count === 0) return tx("custom.countNone", "No account connected");
  if (count === 1) return tx("custom.countOne", "1 account connected");
  return tx("custom.countMany", "{{count}} accounts connected", { count });
}

function instanceDisplayName(instance: NanobotChannelInstanceInfo): string {
  return instance.display_name?.trim() || instance.name.trim() || instance.id;
}
