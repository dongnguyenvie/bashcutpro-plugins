# BashCutPro plugins

Out-of-process plugins for [BashCutPro](https://github.com/dongnguyenvie/BashCutPro) (a fork of FilmCraft) and their
registry. A plugin is a separate process: it reads state through BashCutPro's MCP server or control channel, does its
work outside the app (translation, Vietnamese TTS, image generation…), and brings the result back with existing
commands. Why and how: [`bashcut/decisions.md`](https://github.com/dongnguyenvie/BashCutPro/blob/main/bashcut/decisions.md).

Status: the first plugin, `translate-captions`, also defines the manifest (`bashcut-plugin.json`) and registry format
([BashCutPro#8](https://github.com/dongnguyenvie/BashCutPro/issues/8)).
