
/* switch-layout modular builder (mod4)
   - Supports configurable RJ45 port grouping (e.g., gaps every 12, 16, etc.)
   - Supports optional left-padding of RJ45 columns to right-justify ports
   - Keeps console aligned via computed shift
*/
(function(){
  function makeRj45Cell(labelText, portNumber, labelBelow, isBlank=false){
    const cell=document.createElement('div');
    cell.className='portcell' + (labelBelow ? ' bottom':'') + (isBlank ? ' blank':'');
    const label=document.createElement('div'); label.className='label'; label.textContent=labelText;
    const port=document.createElement('div'); port.className='port inactive'; port.dataset.port=String(portNumber||'');
    cell.appendChild(label); cell.appendChild(port);
    return cell;
  }

  function makeSfpCell(labelText, key, labelBelow){
    const cell=document.createElement('div');
    cell.className='sfp-cell' + (labelBelow ? ' bottom':'');
    const label=document.createElement('div'); label.className='label'; label.textContent=labelText;
    const port=document.createElement('div'); port.className='sfp-port inactive'; port.dataset.uplink=key;
    cell.appendChild(label); cell.appendChild(port);
    return cell;
  }

  function cssPx(varName){
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const n = parseFloat(v.replace('px',''));
    return Number.isFinite(n) ? n : 0;
  }

  function groupPortsToCols(groupPorts){
    return Math.max(0, Math.floor(groupPorts / 2));
  }

  function countGapsBefore(groupsCols, blankColsLeft){
    let remaining = blankColsLeft;
    let gaps = 0;
    for(let i=0; i<groupsCols.length; i++){
      if(remaining <= 0) break;
      const consume = Math.min(groupsCols[i], remaining);
      remaining -= consume;
      if(remaining > 0 && consume === groupsCols[i] && i < groupsCols.length - 1){
        gaps += 1;
      }
    }
    return gaps;
  }

  function buildRj45Row(rowEl, cfg, labelBelow, startPort){
    const groupPorts = cfg.rj45GroupPorts || [12,12,12,12];
    const groupsCols = groupPorts.map(groupPortsToCols);

    const blankColsLeft = Math.max(0, Math.floor((cfg.padLeftPorts || 0) / 2));
const maxPort = cfg.portCount || 48;
const chassisPortCount = cfg.chassisPortCount || 48;


    let port = startPort;
    let globalCol = 0;

    for(let gi=0; gi<groupsCols.length; gi++){
      const g = document.createElement('div');
      g.className = 'port-group';

      for(let ci=0; ci<groupsCols[gi]; ci++){
        const isBlankCol = globalCol < blankColsLeft;
        if(isBlankCol || port > maxPort){
          g.appendChild(makeRj45Cell("", "", labelBelow, true));
        }else{
          g.appendChild(makeRj45Cell(String(port), port, labelBelow, false));
          port += 2;
        }
        globalCol += 1;
      }

      rowEl.appendChild(g);
    }
  }

  function buildUplinks(sfpBlock, uplinkLabels, splitOddEven){
    sfpBlock.innerHTML='';
    const labels = (uplinkLabels && uplinkLabels.length) ? uplinkLabels : ['x1','x2','x3','x4'];

    if(splitOddEven){
      const t=document.createElement('div'); t.className='sfp-row top';
      const b=document.createElement('div'); b.className='sfp-row bottom';
      const odds=labels.filter((_,i)=>i%2===0);
      const evens=labels.filter((_,i)=>i%2===1);

      odds.forEach(x=>t.appendChild(makeSfpCell(x,x,false)));
      evens.forEach(x=>b.appendChild(makeSfpCell(x,x,true)));

      sfpBlock.appendChild(t);
      if(evens.length) sfpBlock.appendChild(b);
    }else{
      const t=document.createElement('div'); t.className='sfp-row top';
      labels.forEach(x=>t.appendChild(makeSfpCell(x,x,false)));
      sfpBlock.appendChild(t);
    }
  }

  window.initSwitchLayout = function(cfg){
    document.title = cfg.title || document.title;

    const rj45Block = document.getElementById('rj45Block');
    const sfpBlock  = document.getElementById('sfpBlock');
    if(!rj45Block || !sfpBlock){
      console.error('[switch-layout mod4] Missing #rj45Block or #sfpBlock');
      return;
    }

    rj45Block.innerHTML='';
    const top=document.createElement('div'); top.className='row top';
    const bottom=document.createElement('div'); bottom.className='row bottom';

    buildRj45Row(top, cfg, false, 1);
    buildRj45Row(bottom, cfg, true, 2);

    rj45Block.appendChild(top); rj45Block.appendChild(bottom);

    const groupPorts = cfg.rj45GroupPorts || [12,12,12,12];
    const groupsCols = groupPorts.map(groupPortsToCols);
    const blankColsLeft = Math.max(0, Math.floor((cfg.padLeftPorts || 0) / 2));

    const rj45w = cssPx('--rj45-w');
    const groupGap = cssPx('--group-gap');
    const gapsBefore = countGapsBefore(groupsCols, blankColsLeft);
    const shiftPx = blankColsLeft * rj45w + gapsBefore * groupGap;

    document.documentElement.style.setProperty('--console-shift-x', (cfg.consoleShiftPx != null ? cfg.consoleShiftPx : shiftPx) + 'px');

    buildUplinks(sfpBlock, cfg.uplinkLabels, !!cfg.splitOddEven);

    console.log('[switch-layout] initSwitchLayout OK (mod4)', {
      portCount: cfg.portCount,
      rj45GroupPorts: groupPorts,
      padLeftPorts: cfg.padLeftPorts || 0,
      blankColsLeft, gapsBefore, shiftPx
    });
  };
})();
