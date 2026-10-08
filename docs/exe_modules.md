# Appendix: deep6.exe modules

The game modules of deep6.exe (Watcom debug info: 209 source files; the
Watcom run-time library is left out). *Functions* / *globals* are the named
symbols of each module. *Cited in* lists the SDK documents that mention the
module, i.e. where its behaviour is described. Roles are only given where
the SDK has looked at the code.

| module | functions | globals | role | cited in |
|---|---|---|---|---|
| _alpha.ASM | 1 | 6 |  |  |
| _edge.ASM | 1 | 0 |  |  |
| _font.ASM | 4 | 0 |  |  |
| _math.ASM | 3 | 0 |  |  |
| _modelp.ASM | 2 | 6 |  |  |
| _mpoly.ASM | 40 | 53 |  |  |
| _pixclr.ASM | 3 | 0 |  |  |
| _span.ASM | 9 | 0 |  |  |
| _swc.ASM | 5 | 0 |  |  |
| _tpoly.ASM | 7 | 0 |  |  |
| _trans.ASM | 3 | 16 |  |  |
| _zbuf.ASM | 3 | 0 |  |  |
| alpha.c | 8 | 2 |  |  |
| anim.c | 12 | 16 |  |  |
| animtex.c | 4 | 0 |  |  |
| asets.c | 25 | 0 |  | formats/models.md |
| assert.c | 1 | 0 |  |  |
| atmos.c | 7 | 17 |  |  |
| atqlist.c | 17 | 12 |  | formats/models.md |
| audioc.c | 67 | 13 | audio device | formats/audio.md |
| automap.c | 38 | 5 | automap drawing |  |
| avi.c | 18 | 13 |  |  |
| bentname.c | 1 | 1 |  |  |
| BinkVid.c | 3 | 2 |  |  |
| bitmap.c | 8 | 4 |  |  |
| blast.ASM | 16 | 1 |  |  |
| blt.c | 0 | 1 |  |  |
| bmp16.c | 2 | 0 |  |  |
| body.c | 11 | 32 |  |  |
| bsp.c | 22 | 22 | BSP drawing |  |
| bsp_phys.c | 17 | 28 |  |  |
| bspcache.c | 2 | 2 |  |  |
| bspfile.c | 39 | 13 | .bsp/.lf/.ls loading | formats/levels.md |
| bspmodel.c | 15 | 22 |  |  |
| bspobj.c | 7 | 2 |  |  |
| bsptex.ASM | 5 | 0 |  |  |
| camera.c | 17 | 0 | camera |  |
| canvas.c | 17 | 0 |  |  |
| cardctrl.c | 97 | 49 | Direct3D renderer control | formats/walls_textures.md |
| cardlite.c | 8 | 7 | coloured lights (.lgt/.rgb) | formats/levels.md |
| cardutil.c | 71 | 7 |  |  |
| cdprot.c | 1 | 0 |  | formats/terrain.md |
| chardemo.c | 43 | 42 |  | formats/databases.md |
| cheatkey.cpp | 1 | 1 |  |  |
| checksum.c | 1 | 0 |  |  |
| cmd.c | 76 | 5 |  |  |
| collide.c | 21 | 16 | collision |  |
| collisio.c | 16 | 25 |  |  |
| color.c | 10 | 0 |  |  |
| combat.c | 135 | 23 | combat, recall/portal effects | formats/audio.md, formats/databases.md, formats/exits.md, formats/models.md |
| compass.c | 3 | 1 |  |  |
| config.c | 58 | 53 | options book |  |
| conlex.c | 3 | 4 |  |  |
| d3dtimer.c | 8 | 9 |  |  |
| d6alloc.c | 6 | 1 |  |  |
| d6glob.c | 0 | 209 |  |  |
| d6iosys.c | 21 | 15 |  |  |
| d6pc.c | 29 | 15 |  |  |
| d6spoke.c | 151 | 155 | spoke loading (LoadSpokeSegment_), entry points, spoke change | formats/data.md, formats/exits.md, formats/levels.md, formats/terrain.md |
| d6string.c | 3 | 63 | D6STRING.DAT | formats/data.md, formats/effects.md |
| d6win95.c | 2 | 1 |  |  |
| d_span.c | 4 | 49 |  | formats/levels.md |
| deep6.c | 53 | 37 | start-up, globals, data loading, town/spoke switch (SetSpokeEntry_) | formats/data.md, formats/databases.md, formats/exits.md, formats/terrain.md, formats/walls_textures.md |
| dialog.c | 23 | 26 |  |  |
| dirfind.c | 4 | 3 |  |  |
| dpcon.c | 29 | 3 |  |  |
| drawhull.c | 6 | 3 |  |  |
| dynlight.c | 12 | 1 |  |  |
| efx.c | 8 | 6 | visual effects (EFX) |  |
| efxcache.c | 12 | 6 |  |  |
| enchants.c | 5 | 0 |  |  |
| entity.c | 5 | 6 |  |  |
| events.c | 73 | 48 | triggers, bound areas, switches, specials, event VM (EVENTS.COD) | formats/data.md, formats/databases.md |
| fileutil.c | 2 | 0 |  |  |
| font.c | 24 | 8 | fonts (.FNT/.p16) |  |
| fountain.c | 3 | 1 |  | formats/databases.md |
| fpufuck.ASM | 3 | 0 |  |  |
| frate.c | 3 | 7 |  |  |
| frustum.c | 4 | 2 | view frustum |  |
| genenc.c | 25 | 26 |  | formats/databases.md |
| globals.c | 0 | 41 |  |  |
| graphobj.c | 26 | 1 |  | formats/models.md |
| guild.c | 53 | 11 | guildmaster VM (GMDATA.PAK) | formats/audio.md, formats/data.md, formats/npc.md |
| indmovie.c | 10 | 1 |  |  |
| jentry.c | 29 | 11 |  | formats/audio.md, formats/databases.md |
| jhp_part.c | 21 | 12 |  | formats/effects.md |
| jhpmap.c | 51 | 10 | automap (.lm/.fog/.mrk) | formats/databases.md, formats/terrain.md |
| journal.c | 14 | 11 | journal (JOURNAL.nnn) |  |
| langutil.c | 4 | 0 |  |  |
| levelmap.c | 54 | 9 |  |  |
| llist.c | 11 | 1 |  |  |
| lmouse.c | 2 | 1 | mouse pointers (.ptr) |  |
| logfile.c | 2 | 1 |  |  |
| mapobj.c | 28 | 17 | debug level viewer (FetchObjects_) | formats/data.md, formats/levels.md |
| mathlib.c | 10 | 0 |  |  |
| mdldata.c | 0 | 5 | model name tables | formats/databases.md, formats/models.md |
| menu.c | 79 | 6 | title and in-game menus |  |
| minegrup.c | 5 | 3 |  |  |
| miptest.c | 11 | 1 |  |  |
| missile.c | 13 | 2 |  |  |
| model.c | 38 | 45 | .mdl loading and drawing | formats/data.md, formats/databases.md, formats/models.md |
| monant.c | 6 | 3 |  | formats/databases.md |
| monster.c | 119 | 48 | monsters (D6MONS) | formats/audio.md, formats/data.md, formats/databases.md, formats/models.md |
| mouse.c | 7 | 12 |  |  |
| mousetim.c | 20 | 36 |  |  |
| MP3Play.c | 11 | 6 |  |  |
| MP3Temp.c | 1 | 0 |  |  |
| mpconn.c | 41 | 13 |  |  |
| mpoly.c | 8 | 5 |  | formats/models.md |
| msgreq.c | 16 | 1 |  |  |
| mshadow.c | 7 | 4 |  |  |
| myscan.ASM | 8 | 0 |  |  |
| nav_sys.c | 8 | 0 | nav graph (.nvs/.l2n/.NAV) | formats/levels.md |
| netcon.c | 2 | 9 |  |  |
| network.c | 332 | 52 | multiplayer messages |  |
| newcam.c | 21 | 26 |  |  |
| newclip.c | 2 | 4 |  |  |
| npc.c | 85 | 53 | NPC dialogue VM (NPCDATA.PAK) | formats/audio.md, formats/data.md, formats/databases.md, formats/models.md, formats/npc.md |
| obj_phys.c | 20 | 18 |  |  |
| objext.c | 6 | 0 |  |  |
| olist.c | 7 | 1 |  |  |
| pakfile.c | 3 | 0 |  |  |
| pal16.c | 17 | 6 |  | formats/models.md, formats/walls_textures.md |
| pal2048c.c | 11 | 7 |  |  |
| pal2k.c | 2 | 0 |  |  |
| palette.c | 3 | 0 |  |  |
| palmap.c | 12 | 3 |  |  |
| palremap.ASM | 2 | 0 |  |  |
| part_sys.c | 10 | 7 |  |  |
| particle.c | 44 | 14 | particle emitters (emitters.dat) | formats/data.md |
| pathai.c | 37 | 14 | path finding, D6LINK | formats/data.md |
| pccreate.c | 130 | 80 | character creation |  |
| pcinvent.c | 67 | 49 | inventory screen | formats/databases.md |
| pcmodel.c | 38 | 30 | PC models, PC snapshots (PCSNAP.nnn) |  |
| pcmounts.c | 4 | 1 |  |  |
| pcorders.c | 18 | 3 |  | formats/databases.md |
| pcskill.c | 11 | 7 |  |  |
| pcspell.c | 8 | 11 |  |  |
| pcstats.c | 6 | 3 |  |  |
| pcstatus.c | 10 | 8 |  |  |
| pcsttext.c | 8 | 12 |  |  |
| pctalk.c | 7 | 1 |  |  |
| pixcolor.c | 8 | 36 |  |  |
| playsam.c | 7 | 10 | music | formats/audio.md |
| pmodel.c | 3 | 0 |  |  |
| pointlgt.c | 14 | 5 |  |  |
| poly.c | 10 | 2 |  |  |
| ppdrawl.c | 6 | 5 |  |  |
| propefx.c | 12 | 1 |  |  |
| pushy.c | 12 | 12 |  | formats/databases.md |
| rain.c | 13 | 11 |  |  |
| random.ASM | 2 | 0 |  | formats/walls_textures.md |
| rayterr.c | 4 | 12 |  | formats/walls_textures.md |
| rgb16.c | 3 | 0 |  |  |
| rotobj.c | 36 | 16 |  |  |
| rpal.c | 10 | 7 |  |  |
| runtime.c | 15 | 21 |  |  |
| scenload.c | 29 | 28 | object lists (.TOL/.BOL/.FOL), object ids | formats/data.md, formats/databases.md, formats/levels.md, formats/models.md, formats/walls_textures.md |
| screen.c | 11 | 19 | DirectDraw screen |  |
| scrncap.c | 1 | 0 |  |  |
| segwrite.c | 33 | 4 | saves: D6SEGnn.GAM, D6ARCHIV.DAT | formats/data.md, formats/exits.md |
| sfxcache.c | 22 | 5 | sound cache | formats/audio.md |
| shadowm.c | 8 | 0 |  |  |
| shopball.c | 17 | 17 |  |  |
| smartcam.c | 11 | 12 |  |  |
| smcoll.c | 18 | 1 |  |  |
| sndread.c | 4 | 3 |  | formats/databases.md |
| soundefx.c | 8 | 13 | sound effects, speech, PC talk | formats/audio.md |
| spelleff.c | 254 | 69 | spell effects |  |
| spells.c | 1 | 2 |  |  |
| sprite.c | 5 | 0 |  |  |
| sr_game.c | 21 | 12 |  |  |
| stars.c | 11 | 12 |  |  |
| sub25616.ASM | 2 | 0 |  |  |
| swcache.c | 13 | 11 |  |  |
| terbsp.c | 15 | 15 | terrain BSP leaf tree (InBSPArea_) |  |
| terrain.c | 37 | 33 | terrain loading and textures | formats/walls_textures.md |
| texlist.c | 10 | 8 | .twd texture wads | formats/levels.md, formats/walls_textures.md |
| textmsg.c | 31 | 44 | TEXTPAK messages | formats/data.md |
| texture.c | 7 | 3 |  |  |
| tfast.c | 2 | 2 |  |  |
| thirdeye.c | 13 | 37 |  |  |
| tnew.c | 11 | 11 | terrain tile drawing, walls, canopy | formats/walls_textures.md |
| townavi.c | 22 | 21 | town hub videos |  |
| towndojo.c | 32 | 4 | dojo |  |
| townhall.c | 27 | 2 | town hall |  |
| townhub.c | 11 | 11 | town hub screen, gates, Gareth | formats/exits.md, formats/terrain.md |
| townmage.c | 50 | 7 | mage guild |  |
| townpawn.c | 32 | 4 | pawn shop |  |
| townsmit.c | 100 | 17 | smithy shop | formats/data.md, formats/databases.md |
| towntmpl.c | 64 | 8 | temple |  |
| towntvrn.c | 11 | 3 | tavern |  |
| townyard.c | 19 | 5 | ship yard |  |
| tpoly.c | 16 | 15 |  |  |
| tpoly2.c | 28 | 15 |  | formats/walls_textures.md |
| tracklen.cpp | 8 | 1 |  |  |
| trange.c | 4 | 0 |  |  |
| trans.c | 8 | 0 |  |  |
| traps.c | 21 | 11 | D6TRAP locks and traps | formats/data.md, formats/databases.md, formats/models.md |
| treasure.c | 15 | 5 | treasure (D6TREAS/D6TRLIST) | formats/databases.md |
| trig.c | 1 | 2 |  |  |
| vector.c | 36 | 0 |  | formats/models.md |
| virtualc.c | 2 | 1 |  |  |
| warp.c | 4 | 7 |  |  |
| wbutton.c | 59 | 1 |  |  |
| wclock.c | 4 | 7 |  |  |
| wcoll.c | 16 | 11 |  | formats/databases.md |
| winmain.c | 7 | 1 |  |  |
| wlight.c | 5 | 4 |  |  |
