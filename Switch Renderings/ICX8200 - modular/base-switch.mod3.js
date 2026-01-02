
/* switch-layout modular builder (mod3) */
(function(){
  function makeSpacer(){ const s=document.createElement('div'); s.className='spacer'; return s; }

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

  // Helpers to read CSS vars as pixels
  function cssPx(varName){
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    // expect like "28px"
    const n = parseFloat(v.replace('px',''));
    return Number.isFinite(n) ? n : 0;
  }

  function buildRow48Grid(rowEl, startPort, maxPort, labelBelow, padGroupsLeft=0){
    // Grid is 4 groups of 6 columns, with 3 spacers between groups.
    // padGroupsLeft: how many full 6-port groups to render as blanks at the left (0..4)
    let port = startPort;
    for(let g=0; g<4; g++){
      for(let i=0;i<6;i++){
        const isPad = g < padGroupsLeft;
        if(!isPad && port <= maxPort){
          rowEl.appendChild(makeRj45Cell(String(port), port, labelBelow, false));
        }else{
          rowEl.appendChild(makeRj45Cell("", "", labelBelow, true));
        }
        if(!isPad) port += 2; // only increment ports when we're placing real columns
      }
      if(g<3) rowEl.appendChild(makeSpacer());
    }
  }

  function buildUplinks(sfpBlock, uplinkLabels, splitOddEven){
    sfpBlock.innerHTML='';
    if(splitOddEven){
      const t=document.createElement('div'); t.className='sfp-row top';
      const b=document.createElement('div'); b.className='sfp-row bottom';
      const odds=uplinkLabels.filter((_,i)=>i%2===0);
      const evens=uplinkLabels.filter((_,i)=>i%2===1);
      odds.forEach(x=>t.appendChild(makeSfpCell(x,x,false)));
      evens.forEach(x=>b.appendChild(makeSfpCell(x,x,true)));
      sfpBlock.appendChild(t); sfpBlock.appendChild(b);
    } else {
      const t=document.createElement('div'); t.className='sfp-row top';
      uplinkLabels.forEach(x=>t.appendChild(makeSfpCell(x,x,false)));
      sfpBlock.appendChild(t);
    }
  }

  // Public entry
  window.initSwitchLayout = function(cfg){
    document.title = cfg.title || document.title;

    const rj45Block = document.getElementById('rj45Block');
    const sfpBlock  = document.getElementById('sfpBlock');
    if(!rj45Block || !sfpBlock){
      console.error('[switch-layout mod3] Missing #rj45Block or #sfpBlock');
      return;
    }

    // Determine how many left pad groups (each group = 12 ports total across both rows)
    // For a 24-port model you asked to "sit next to the uplinks", so we right-justify the RJ45 block
    // by padding 2 groups (24 ports worth) on the left within the 48-port grid.
    const padGroupsLeft = (cfg.padGroupsLeft ?? (cfg.portCount === 24 ? 2 : 0));

    // Build RJ45 rows
    rj45Block.innerHTML='';
    const top=document.createElement('div'); top.className='row top';
    const bottom=document.createElement('div'); bottom.className='row bottom';

    // Top row is odds; bottom is evens
    // For 48 ports, odds go to 47; for 24 ports, odds go to 23, evens to 24.
    const maxPort = cfg.portCount || 48;

    buildRow48Grid(top, 1, maxPort- (maxPort%2===0 ? 1:0), false, padGroupsLeft);
    buildRow48Grid(bottom, 2, maxPort, true, padGroupsLeft);

    rj45Block.appendChild(top); rj45Block.appendChild(bottom);

    // Shift console by the same left padding width so console stays snug with RJ45
    // pad width = (padGroupsLeft*6 columns)*rj45-w + (padGroupsLeft>0 ? (padGroupsLeft) spacers?) + group gaps
    // For 2 pad groups: 12 columns + 2 group gaps (between group1-2 and group2-3)
    const rj45w = cssPx('--rj45-w');
    const groupGap = cssPx('--group-gap');
    let shiftPx = 0;
    if(padGroupsLeft > 0){
      const padCols = padGroupsLeft * 6;
      const padGaps = padGroupsLeft; // number of group gaps before the first real group (for 2 => gaps after g1 and g2)
      shiftPx = padCols * rj45w + padGaps * groupGap;
    }
    if(cfg.consoleShiftPx != null){
      shiftPx = cfg.consoleShiftPx;
    }
    document.documentElement.style.setProperty('--console-shift-x', shiftPx + 'px');

    // Build uplinks
    buildUplinks(sfpBlock, cfg.uplinkLabels || ['x1','x2','x3','x4'], !!cfg.splitOddEven);

    console.log('[switch-layout] initSwitchLayout OK (mod3)', {padGroupsLeft, shiftPx});
  };
})();
