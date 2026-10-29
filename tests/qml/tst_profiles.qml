import QtQuick
import QtTest
import Quickshell
import "../stable" as Stable
import "../dev" as Dev
import "../edge" as Edge

Item {
 width: 1024; height: 768
 Component { id: stableOverlay; Stable.Dms {} }
 Component { id: devOverlay; Dev.Dms {} }
 Component { id: edgeOverlay; Edge.Dms {} }
 Component { id: devBar; Dev.BarWidget {} }
 QtObject {
  id: shell
  property string hidden: ""
  function hide(id) { hidden = id }
 }
 TestCase {
  name: "DmsProfiles"
  when: windowShown
  property var objects: []
  function make(component,properties) {
   var object=component.createObject(parent,properties || {})
   verify(object!==null);objects.push(object);return object
  }
  function cleanup() {
   for(var i=objects.length-1;i>=0;i--) objects[i].destroy()
   objects=[];wait(0);Quickshell.lastCommand=[];shell.hidden=""
   Stable.State.ship="";Dev.State.ship="";Edge.State.ship=""
  }
  function test_overlayRoutesAndLabels() {
   var cases=[{component:stableOverlay,id:"urbit.dms",layer:"urbit-dms",name:"Urbit DMs"},
    {component:devOverlay,id:"urbit.dms.dev",layer:"urbit-dms-dev",name:"Urbit DMs (dev)"},
    {component:edgeOverlay,id:"urbit.dms.edge",layer:"urbit-dms-edge",name:"Urbit DMs (edge)"}]
   for(var i=0;i<cases.length;i++) {
    var row=cases[i],overlay=make(row.component,{shell:shell,manifest:{id:row.id}})
    compare(overlay.moduleName,row.id);compare(overlay.layerNamespace,row.layer);compare(overlay.instanceLabel,row.name);compare(overlay.displayName("~nec"),"~nec")
    overlay.contacts={"~nec":"Test contact"};compare(overlay.displayName("~nec"),"Test contact")
    overlay.opened=true;overlay.dismiss();compare(shell.hidden,row.id);verify(!overlay.opened)
   }
  }
  function test_lateManifestInjectionFollowsNativeHost() {
   var overlay=make(devOverlay,{shell:shell})
   overlay.manifest={id:"urbit.dms.dev"}
   compare(overlay.moduleName,"urbit.dms.dev");compare(overlay.layerNamespace,"urbit-dms-dev")
   overlay.dismiss();compare(shell.hidden,"urbit.dms.dev")
  }
  function test_barPopupFollowsInjectedModule() {
   var bar=make(devBar,{moduleName:"urbit.dms.dev"})
   var loader=findChild(bar,"dmsRecentPanelLoader");verify(loader!==null)
   tryVerify(function(){return loader.item!==null})
   compare(loader.item.moduleName,"urbit.dms.dev");compare(loader.item.displayName,"Urbit DMs (dev)")
   loader.item.launchInbox();compare(Quickshell.lastCommand,["omarchy-shell","shell","summon","urbit.dms.dev",'{"mode":"inbox"}'])
   // Native hosts may finish injecting identity after the nested loader runs.
   bar.moduleName="urbit.dms.edge"
   compare(loader.item.moduleName,"urbit.dms.edge");compare(bar.displayName,"Urbit DMs (edge)")
   loader.item.launchInbox();compare(Quickshell.lastCommand[3],"urbit.dms.edge")
  }
  function test_revocationShowsLoginAndDropsDelayedData() {
   var overlay=make(devOverlay,{shell:shell,manifest:{id:"urbit.dms.dev"}})
   overlay.onLine(JSON.stringify({event:"login",ship:"~zod"}))
   overlay.onLine(JSON.stringify({event:"inbox",rows:[{ship:"~nec",text:"hello"}]}))
   overlay.directSignIn=true;overlay.busy=true;overlay.pendingSend=99
   overlay.unresolvedOutbound=[{request:"retained-unknown"}]
   overlay.onLine(JSON.stringify({event:"logout",revoked:true}))
   compare(overlay.ship,"");compare(Dev.State.ship,"");compare(overlay.rows.length,0)
   verify(overlay.opened);verify(!overlay.directSignIn);verify(!overlay.busy)
   compare(overlay.pendingSend,-1);compare(overlay.unresolvedOutbound.length,1)
   overlay.onLine(JSON.stringify({event:"inbox",rows:[{ship:"~nec"}]}))
   compare(overlay.rows.length,0);compare(Dev.State.rows.length,0)
  }
  function test_stateIsSeparateForThreeSourceRoots() {
   make(stableOverlay,{manifest:{id:"urbit.dms"}})
   make(devOverlay,{manifest:{id:"urbit.dms.dev"}})
   make(edgeOverlay,{manifest:{id:"urbit.dms.edge"}})
   Stable.State.ship="~zod";Dev.State.ship="~nec";Edge.State.ship="~bud"
   compare(Stable.State.ship,"~zod");compare(Dev.State.ship,"~nec");compare(Edge.State.ship,"~bud")
   verify(Stable.State.sendHandler!==Dev.State.sendHandler)
   verify(Dev.State.sendHandler!==Edge.State.sendHandler)
  }
 }
}
