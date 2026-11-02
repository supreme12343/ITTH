
        var comp = app.project.activeItem;
        if (comp && comp instanceof CompItem) {
            app.beginUndoGroup("AI Action");
            var p = "colour grading".toLowerCase();

            if (p.indexOf("text") !== -1) {
                comp.layers.addText("colour grading");
            } else if (p.indexOf("glow") !== -1) {
                var adj = comp.layers.addSolid([1,1,1], "AI_Glow", comp.width, comp.height, comp.pixelAspect, comp.duration);
                adj.adjustmentLayer = true;
                adj.property("Effects").addProperty("ADBE Glow");
            } else if (p.indexOf("color") !== -1 || p.indexOf("grade") !== -1) {
                var adj = comp.layers.addSolid([1,1,1], "AI_Color", comp.width, comp.height, comp.pixelAspect, comp.duration);
                adj.adjustmentLayer = true;
                adj.property("Effects").addProperty("ADBE Curves");
            } else if (p.indexOf("render") !== -1) {
                app.project.renderQueue.items.add(comp);
            }

            app.endUndoGroup();
        } else {
            alert("Please select a Composition timeline first!");
        }
        