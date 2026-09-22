/**
 * Adds Claude models released after the SillyTavern build to the Claude source
 * model picker. The picker is a static list in public/index.html, so a new model
 * id cannot be selected from the UI until it is added to that list.
 */
import { eventSource, event_types } from '../../../events.js';

const SELECT_ID = 'model_claude_select';

// Insert each id directly after the model it supersedes, matching the picker's own order.
const EXTRA_MODELS = [
    { id: 'claude-opus-5-5', after: 'claude-opus-5' },
];

function findOption(select, value) {
    for (const option of select.options) {
        if (option.value === value) {
            return option;
        }
    }

    return null;
}

function injectExtraModels() {
    const select = document.getElementById(SELECT_ID);

    if (!select) {
        return;
    }

    const group = select.querySelector('optgroup') || select;

    for (const model of EXTRA_MODELS) {
        if (findOption(select, model.id)) {
            continue;
        }

        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.id;

        const anchor = model.after ? findOption(select, model.after) : null;

        if (anchor && anchor.parentElement === group) {
            anchor.after(option);
        } else {
            group.appendChild(option);
        }

        console.debug('claude-model-extras: added ' + model.id + ' to #' + SELECT_ID);
    }
}

injectExtraModels();
eventSource.on(event_types.APP_READY, injectExtraModels);
eventSource.on(event_types.SETTINGS_LOADED, injectExtraModels);
