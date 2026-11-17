import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
Config.setPublicDir('assets');
Config.setChromiumOpenGlRenderer('angle');
Config.setJpegQuality(96);
