import { LightningElement, track, wire, api } from 'lwc';
import invokeItemDetailFlow from '@salesforce/apex/ItemSelectionHandlerNew.invokeItemDetailFlow';
import getGuidedSellingAdminDetails from '@salesforce/apex/Offer360AppHandler.getGuidedSellingAdminDetails';
import getObjectFields from '@salesforce/apex/ChatbotFieldHelper.getObjectFields';
import { ShowToastEvent } from 'lightning/platformShowToastEvent';
import { predefinedMessages } from './predefinedMsgs';
import { inputJSON } from './predefinedMsgs';
import ICON from '@salesforce/resourceUrl/chatBotCartIcon';
import pubsub from "c/pubsub";


export default class ChatbotLauncher extends LightningElement {

    // Property for the PNG icon URL
    //iconUrl = '/resource/1750674178000/chatBotCartIcon'; 
    iconUrl = ICON;

    @api recordId;

    @track showPopup = false;
    @track metadataRecords;
    @track welcomeMessage;
    @track action1;
    @track action2;
    @track action3;
    @track chatMessage;
    undefinedEventErMsg = 'Unknown event, check again';
    resourceUrl;

    isChatExpanded = false;
    showProductCatalog = false;
    showLineItemsView = false;
    showGuidedSelling = false;
    showUnhandledEvent = false;
    chatbotContext = true;
    eventHeaderMsg = '';
    question = ''; // Holds the user inputchange the
    chatMessages = []; // Array to store the chat messages
    parsedMessage;
    chatBotEvent;
    chatBotPayload;
    @track selectedFile = null;
    @track selectedFileContent = null;
    displayMessageForBubble = '';

    itemDetailFlowInputVariables;
    productDetails;
    @track invokeItemDetailFlow = false;
    @track invokeAddNewItemJourney = false;
    @track invokeFlow = false;
    @track itemDetailFlowName = false;
    @track fieldDataList = [];
    @track modalStyle = '';
    @track outputJson;
    @track productDetails = null;
    @track itemDetailFlowInputVariables;
    adsalescustomData = {};


    viewName = 'QuoteLineitemView';
    ItemselectionName;
    metadataDefinition;
    objectApiName;

    togglePopup() {
        this.showPopup = !this.showPopup;
    }

    toggleExpand() {
        this.isChatExpanded = !this.isChatExpanded;
    }

    get iconName() {
        return this.isChatExpanded ? 'utility:left' : 'utility:right';
    }

    get alternativeText() {
        return this.isChatExpanded ? 'Collapse Sidebar' : 'Expand Sidebar';
    }

    toggleSection() {
        //this.isSidebarCollapsed = !this.isSidebarCollapsed;
        this.isChatExpanded = !this.isChatExpanded;
        // Add any additional logic for toggling the sidebar here
    }

    async connectedCallback() {

        console.log('AAK2105 ChatBotLauncher2 recordId=>' + this.recordId);
        console.log('AAK2105 ChatBotLauncher2 viewName=>' + this.viewName);
        console.log('AAK2105 ChatBotLauncher2 objectApiName=>' + this.objectApiName);
        console.log('AAK2105 ChatBotLauncher2 ItemselectionName=>' + this.ItemselectionName);
        console.log('AAK2105 ChatBotLauncher2 metadataDefinition=>' + JSON.stringify(this.metadataDefinition));

        console.log('AAK2105 Loaded JSON data:', JSON.stringify(predefinedMessages));
        this.myData = predefinedMessages;
        if (Array.isArray(predefinedMessages.Messages)) {
            const unknownEventMessage = predefinedMessages.Messages.find(
                (msg) => msg.msgName === 'unknownEvent' && msg.msgDesc
            );
            console.log('AAK2105 unknownEventMessage', JSON.stringify(unknownEventMessage));
            if (unknownEventMessage) {
                this.undefinedEventErMsg = unknownEventMessage.msgDesc;
            }
            console.log('AAK2105 undefinedEventErMsg', JSON.stringify(this.undefinedEventErMsg));
        }
    }

    handleError(errorMessage) {
        console.error(errorMessage);
        this.showErrorToast('Error', errorMessage);
    }

    showErrorToast(title, message) {
        this.dispatchEvent(new ShowToastEvent({ title, message, variant: 'error' }));
    }

    triggerFileUploadExpanded() {
        const fileInput = this.template.querySelector('[data-id="fileInputExpanded"]');
        if (fileInput) fileInput.click();
    }

    triggerFileUploadNonExpanded() {
        const fileInput = this.template.querySelector('[data-id="fileInputNonExpanded"]');
        if (fileInput) fileInput.click();
    }

    removeFile() {
        this.selectedFile = null;
        this.selectedFileContent = null;
        // Clear inputs so the same file can be selected again
        const expInput = this.template.querySelector('[data-id="fileInputExpanded"]');
        if(expInput) expInput.value = '';
        const nonExpInput = this.template.querySelector('[data-id="fileInputNonExpanded"]');
        if(nonExpInput) nonExpInput.value = '';
    }

    handleFileSelected(event) {
        const file = event.target.files[0];
        if (file) {
            console.log('File selected:', file.name);
            this.selectedFile = file;
            
            const reader = new FileReader();
            reader.onload = () => {
                this.selectedFileContent = reader.result;
                console.log('File content processed and ready for LLM call.');
            };

            // Read text formats as text, else read as Base64 Data URL for binary APIs
            if (file.name.toLowerCase().endsWith('.csv') || file.name.toLowerCase().endsWith('.txt')) {
                reader.readAsText(file);
            } else {
                reader.readAsDataURL(file);
            }
        }
    }

    handleOnChange(event) {
        this.question = event.target.value;
    }

    handleOnClick() {
        let payloadForBackend = this.question;
        this.displayMessageForBubble = this.question;
        
        // Append file content to the LLM payload if a file was attached
        if (this.selectedFile && this.selectedFileContent) {
            payloadForBackend += `\n\n--- Attached File: ${this.selectedFile.name} ---\n${this.selectedFileContent}`;
            this.displayMessageForBubble = `[Attached: ${this.selectedFile.name}]\n` + this.question;
        }

        if (payloadForBackend.trim() !== '') {
            this.currentQuestion = payloadForBackend; // Store the original or combined question
            try {
                this.parsedMessage = JSON.parse(payloadForBackend);
                console.log('parsedMessage parsing success=>' + this.parsedMessage);
                this.resetFlags();
                this.handleParsedMessage();
                
                // Add to chat here since handleInputKeywords is not called on successful parse
                const newMessage = {
                    id: this.chatMessages.length + 1,
                    text: this.displayMessageForBubble,
                    isError: false
                };
                this.chatMessages = [...this.chatMessages, newMessage];
                this.question = '';
            }
            catch (error) {
                // If parsing fails, just use it as plain text
                this.parsedMessage = payloadForBackend;
                console.log('parsedMessage parsing failed=>' + error.message);
                this.handleInputKeywords(payloadForBackend);
            }
            
            // Clear the file state after sending
            this.removeFile();
        }
    }

    resetFlags() {
        this.showProductCatalog = false;
        this.showLineItemsView = false;
        this.showGuidedSelling = false;
        this.showUnhandledEvent = false;
        this.isChatExpanded = false;
        this.eventHeaderMsg = '';
        this.chatBotEvent = '';
        this.chatBotPayload = '';
    }

    handleInputKeywords(question) {
        let inputKeyword = question;

        if (Array.isArray(inputJSON.Events)) {
            const keywordEvent = inputJSON.Events.find((ev) => ev.keyword === inputKeyword && ev.event && ev.payload);
            console.log('AAK2105 inputJSON Events keywordEvent', JSON.stringify(keywordEvent));
            if (keywordEvent) {
                this.parsedMessage = keywordEvent;
            } else {
                // AI INTEGRATION: If no strict keyword matches, assume natural language product request.
                // We construct a 'launchGuidedSelling' event to trigger the AI parsing workflow.
                console.log('No strict keyword match. Proceeding with AI natural language parsing.');
                this.parsedMessage = {
                    event: "launchGuidedSelling",
                    payload: {
                        cartId: this.recordId,
                        priceBookEntryId: "01uNS000002jiGgYAI", // Mock valid ID to bypass Salesforce validation
                        itemDetailFlowName: "GetProductDetails"
                    }
                };
            }
            console.log('handleInputKeywords parsedMessage parsing success=>' + JSON.stringify(this.parsedMessage));
            this.resetFlags();
            this.handleParsedMessage();
        }

        const newMessage = {
            id: this.chatMessages.length + 1, // Use length to ensure a unique id
            text: this.displayMessageForBubble || this.question,
            isError: false
        };
        this.chatMessages = [...this.chatMessages, newMessage];
        this.question = '';
        this.displayMessageForBubble = '';
    }

    handleParsedMessage() {

        this.chatBotEvent = this.parsedMessage?.event || null;
        this.chatBotPayload = this.parsedMessage?.payload || null;
        console.log('AAK2105 chatBotEvent=>' + this.chatBotEvent);
        console.log('AAK2105 chatBotPayload=>' + JSON.stringify(this.chatBotPayload));

        switch (this.chatBotEvent) {
            case 'showCatalog':
                this.handleShowCatalog();
                break;
            case 'launchGuidedSelling':
                this.handleGuidedSelling(this.chatBotPayload);
                break;
            case 'loadLineItemsView':
                this.handleLineItemsView();
                break;
            default:
                this.handleDefault();
        }
    }

    handleDefault() {
        this.showUnhandledEvent = true;
        this.eventHeaderMsg = this.undefinedEventErMsg;
        this.isChatExpanded = false;
        const newMessage = {
            id: this.chatMessages.length + 1, // Use length to ensure a unique id
            text: this.eventHeaderMsg,
            isError: true
        };
        this.chatMessages = [...this.chatMessages, newMessage];
    }

    handleShowCatalog() {

        this.eventHeaderMsg = 'Show Product Catalog';
        this.isChatExpanded = true;
        console.log('ChatbotLauncher handleShowCatalog showProductCatalog=>' + this.showProductCatalog);
        console.log('ChatbotLauncher handleShowCatalog chatBotPayload=>' + JSON.stringify(this.chatBotPayload));
        this.recordId = this.chatBotPayload.cartId;
        this.ItemselectionName = this.chatBotPayload.itemSelectionAdmin;
        console.log('ChatbotLauncher handleShowCatalog recordId=>' + this.recordId);
        console.log('ChatbotLauncher handleShowCatalog ItemselectionName=>' + this.ItemselectionName);
        this.showProductCatalog = true;

    }

    handleGuidedSelling(payload) {
        //this.showGuidedSelling = true;
        this.eventHeaderMsg = 'Launch Guided Selling';
        this.isChatExpanded = true;
        console.log('showGuidedSelling=>' + this.showGuidedSelling);
        console.log('AAK2105 payload=>' + JSON.stringify(payload));
        let productDataKey;
        if (payload?.itemDetailFlowName) {
            this.itemDetailFlowName = payload.itemDetailFlowName;
        }
        if (payload?.priceBookEntryId) {
            productDataKey = payload.priceBookEntryId;
        }
        console.log('AAK2105 itemDetailFlowName=>' + this.itemDetailFlowName);
        console.log('AAK2105 productDataKey=>' + productDataKey);
        // Guided Selling
        if (this.itemDetailFlowName) {
            this.invokeItemDetailFlow = true;
            this.invokeFlow = false;
            this.itemDetailFlowInputVariables = {
                cartId: this.recordId,
                itemId: productDataKey
            };
            console.log('AAK2105 itemDetailFlowInputVariables=>' + JSON.stringify(this.itemDetailFlowInputVariables));
            let itemDetailFlowInputJSON = JSON.stringify(this.itemDetailFlowInputVariables);
            console.log('1111111', JSON.stringify(itemDetailFlowInputJSON));
            this.invokeFlowMethod(this.itemDetailFlowName, itemDetailFlowInputJSON); // Pass the JSON string directly
        }

    }

    async invokeFlowMethod(flowName, inputJson) {
        console.log('ChatbotLauncher invokeFlowMethod flowName from invokeFlowMethod ::' + flowName);
        console.log('ChatbotLauncher invokeFlowMethod inputJson from invokeFlowMethod ::' + inputJson);
        try {
            const result = await invokeItemDetailFlow({
                flowApiname: flowName,
                itemDetailFlowInputVariables: inputJson // pass the JSON string directly
            });
            this.outputJson = JSON.parse(result).result;
            console.log('ChatbotLauncher invokeFlowMethod outputJson ::' + JSON.stringify(this.outputJson));
            this.productDetails = this.outputJson;

            // AI INTEGRATION MOCK: If the Apex flow returned null (because of mock product ID), mock the productDetails for the AI demo
            if (!this.productDetails) {
                console.log('Flow returned null. Mocking productDetails for AI demo.');
                this.productDetails = {
                    productId: '01tdN0000085NtpQAE',
                    family: 'Internet'
                };
            }

            console.log('ChatbotLauncher AAK2105  productDetails::' + JSON.stringify(this.productDetails));
            //this.showGuidedSelling = true;
            //this.showProductCatalog = false;//incase if guyided selling lauched from catalog
            if (this.productDetails) {
                //let guidedSellingAdminId = this.productDetails?.guidedSellingAdminId;
                let productId = this.productDetails?.productId;

                if (productId) {
                    // ps commented
                    // const guidedSellingAdminDetails = await getGuidedSellingAdminDetails({ productId });
                    const guidedSellingAdminDetails = await this.generateGuidedSellingJSONFromAI(this.recordId, this.currentQuestion || this.question);
                    console.log('ChatbotLauncher guidedSellingAdminDetails ::' + JSON.stringify(guidedSellingAdminDetails));
                    this.invokeAddNewItemJourney = true;

                    this.showGuidedSelling = true;
                    this.showProductCatalog = false;
                    console.log('ChatbotLauncher invokeFlowMethod showGuidedSelling=>' + this.showGuidedSelling);

                    if (guidedSellingAdminDetails) {
                        console.log('ChatbotLauncher if guidedSellingAdminDetails true::' + JSON.stringify(guidedSellingAdminDetails));
                        console.log('ChatbotLauncher AAk2105 family=>' + this.productDetails.family);
                        console.log('ChatbotLauncher AAK2105 adsalescustomData=>' + JSON.stringify(this.adsalescustomData));
                        this.adsalescustomData[this.productDetails.family] = JSON.parse(guidedSellingAdminDetails.DUMMY_OfferUIDefinition__c);
                        console.log('ChatbotLauncher AAk2105 family=>' + this.productDetails.family);
                        console.log('ChatbotLauncher PS123 guidedSellingAdminDetails=>' + guidedSellingAdminDetails.DUMMY_Title__c);

                        this.adsalescustomData['CommonTitle'] = guidedSellingAdminDetails.DUMMY_Title__c;
                        console.log('ChatbotLauncher PS123 adsalescustomData=>' + JSON.stringify(this.adsalescustomData));
                        this.adsalescustomData['CustomActionFlowName'] = JSON.parse(guidedSellingAdminDetails.DUMMY_OfferUIDefinition__c).CustomActionFlowName;

                        console.log('ChatbotLauncher AAK2105 adsalescustomData=>' + JSON.stringify(this.adsalescustomData));
                        //this.showGuidedSelling = true;
                        //this.showProductCatalog = false;//incase if guyided selling lauched from catalog
                    }
                } else {
                    this.invokeAddNewItemJourney = true;
                    this.adsalescustomData[this.productDetails.family] = null;
                    this.adsalescustomData['CommonTitle'] = 'Add Product';
                    this.adsalescustomData['CustomActionFlowName'] = null;
                }
            }
        }
        catch (error) {
            console.error('Error invoking flow:', JSON.stringify(error));
            if (error.body && error.body.message) {
                console.error('Apex error message:', error.body.message);
            } else {
                console.error('An unexpected error occurred:', error);
            }
        }
    }

    handleLineItemsView() {
        this.showLineItemsView = true;
        this.eventHeaderMsg = 'Load Line Items View';
        this.isChatExpanded = true;
        console.log('showLineItemsView=>' + this.showLineItemsView);
    }

    handleaddproductInvoke(event) {
        this.isaddProduct = event.detail;
        if (this.isaddProduct) {
            const changeEvent = new CustomEvent('recordChange', {});
            this.dispatchEvent(changeEvent);
        }
    }

    handleFinishAddFlow(event) {
        this.invokeAddNewItemJourney = false;
        this.invokeFlow = false;
    }

    handleGuidSellCat(event) {
        console.log('Received in handleGuidSellCat:', JSON.stringify(event.detail));
        this.parsedMessage = event.detail;
        //this.parsedMessage = keywordEvent;
        console.log('handleGuidSellCat parsedMessage parsing success=>' + JSON.stringify(this.parsedMessage));
        this.resetFlags();
        this.handleParsedMessage();
    }

    handleLineItemSave(event) {
        console.log('Received in handleLineItemSave:', JSON.stringify(event.detail));
        this.parsedMessage = event.detail;
        //this.parsedMessage = keywordEvent;
        console.log('handleLineItemSave parsedMessage parsing success=>' + JSON.stringify(this.parsedMessage));
        this.resetFlags();
        this.handleParsedMessage();
    }

    handleIconClick() {
        this.isFeatureEnabled = !this.isFeatureEnabled;
        console.log('Feature flag enabled:', this.isFeatureEnabled);
        if (this.isFeatureEnabled == true) {
            let inputKeyword = 'LineItemsView1';
            if (Array.isArray(inputJSON.Events)) {
                const keywordEvent = inputJSON.Events.find((ev) => ev.keyword === inputKeyword && ev.event && ev.payload);
                console.log('AAK2105 inputJSON Events keywordEvent', JSON.stringify(keywordEvent));
                this.parsedMessage = keywordEvent;
                console.log('handleInputKeywords parsedMessage parsing success=>' + JSON.stringify(this.parsedMessage));
                this.resetFlags();
                this.handleParsedMessage();
            }
        }
        else {
            this.resetFlags();
        }

    }

    async generateGuidedSellingJSONFromAI(recordId, userQuestion) {

        try {
            // 1. Find Salesforce Object and Child Object based on recordId prefix
            let objectApiName = '';
            let childObjectApiName = '';
            const prefix = recordId ? String(recordId).substring(0, 3) : '';

            if (prefix === '0Q0') {
                objectApiName = 'Quote';
                childObjectApiName = 'QuoteLineItem';
            } else if (prefix === '801') {
                objectApiName = 'Order';
                childObjectApiName = 'OrderItem';
            } else {
                objectApiName = 'Quote';
                childObjectApiName = 'QuoteLineItem';
            }

            console.log('Detected Object:', objectApiName, 'Child Object:', childObjectApiName);

            // 2. Dynamically Query Fields for the Child Object
            let availableFields = [];
            try {
                availableFields = await getObjectFields({ objectName: childObjectApiName });
                console.log('Dynamically fetched ' + availableFields.length + ' fields for ' + childObjectApiName);
            } catch (err) {
                console.error('Error fetching fields dynamically, using fallback', err);
                // Fallback just in case
                availableFields = [
                    { name: 'Quantity', type: 'number', label: 'Quantity' },
                    { name: 'UnitPrice', type: 'number', label: 'Sales Price' },
                    { name: 'Description', type: 'Text', label: 'Line Description' }
                ];
            }

            // 3. Create the Prompt
            //             const systemPrompt = `
            // You are an intelligent Salesforce configuration assistant.
            // Your task is to map a user's natural language request to a specific Salesforce JSON UI definition for guided selling.

            // INPUTS:
            // 1. Target Child Object API Name.
            // 2. Available Fields (List of field API names, types, labels, and picklistOptions if applicable).
            // 3. User Question (Natural language request containing values).

            // INSTRUCTIONS:
            // 1. Identify which available fields correspond to the values mentioned in the user's question.
            // 2. Extract the values and assign them to the "defaultValue" property. If a field is not mentioned, do not set a defaultValue or set it to null.
            // 3. If a field is a PICKLIST or MULTIPICKLIST, you MUST map the user's requested value to one of the exact options provided in the "picklistOptions" array. If the user says "100" and the option is "100 MB", set defaultValue to "100 MB".
            // 4. Determine the appropriate "uiElement" based on the field type. It MUST be one of the following exact strings: "Text", "checkbox", "number", "picklist", "date", or "lookup".
            // 5. Return ONLY valid JSON matching the exact structure below, without any markdown formatting or extra text.

            // REQUIRED OUTPUT JSON STRUCTURE:
            // {
            //   "Section": [
            //     {
            //       "SectionLabel": "Line Item Details",
            //       "SectionName": "LineItemDetails",
            //       "SectionSequence": 2,
            //       "category": "<Target Child Object API Name>",
            //       "isSectionOpen": true,
            //       "DataSource": [
            //         {
            //           "name": "<Field API Name>",
            //           "label": "<Field Label>",
            //           "uiElement": "<UI Element Type>",
            //           "isRequired": <true or false>,
            //           "defaultValue": <Extracted Value>
            //         }
            //       ]
            //     }
            //   ],
            //   "EnableCustomAction": false,
            //   "CustomActionFlowName": null
            // }`;


            const systemPrompt = `
You are a JSON generation engine.

Return ONLY one valid JSON object.
Do not explain.
Do not analyze.
Do not include thinking.
Do not include markdown.
Do not include code fences.
Do not include comments.
Do not include text before or after JSON.

Create a Salesforce guided selling UI JSON using only fields from Available Fields.

Rules:
1. Include only fields that are clearly mentioned in the user question.
2. Always use exact field API names from Available Fields.
3. For PICKLIST fields, defaultValue must match one of the exact picklistOptions.
4. uiElement mapping:
   DOUBLE, INTEGER, CURRENCY, PERCENT -> "number"
   DATE -> "date"
   BOOLEAN -> "checkbox"
   PICKLIST, MULTIPICKLIST -> "picklist"
   REFERENCE -> "lookup"
   STRING, TEXTAREA -> "Text"

Output shape:
{
  "Section": [
    {
      "SectionLabel": "Line Item Details",
      "SectionName": "LineItemDetails",
      "SectionSequence": 2,
      "category": "<Target Child Object API Name>",
      "isSectionOpen": true,
      "DataSource": [
        {
          "name": "<Field API Name>",
          "label": "<Field Label>",
          "uiElement": "<UI Element Type>",
          "isRequired": false,
          "defaultValue": <value>
        }
      ]
    }
  ],
  "EnableCustomAction": false,
  "CustomActionFlowName": null
}
`;

            const userPrompt = `User Question: "${userQuestion}" Target Child Object: "${childObjectApiName}" Available Fields: ${JSON.stringify(availableFields)}`;
            console.log('System Prompt:\n', systemPrompt);
            console.log('User Prompt:\n', userPrompt);

            const startTime = Date.now();


            //Ollama
            const response = await fetch('https://mendy-myological-electronically.ngrok-free.dev/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'ngrok-skip-browser-warning': 'true'
                },
                body: JSON.stringify({
                    model :'gemma4:latest',
                    //model: 'qwen2.5vl:7b',
                    //model: 'qwen2.5-coder:7b',
                    model: 'DeepSeek-V3.1',
                    messages: [
                        { role: 'system', content: systemPrompt },
                        { role: 'user', content: userPrompt }
                    ],
                    stream: false,
                    // Important for Qwen reasoning models if supported by your backend/Ollama version
                    think: false,
                    options: {
                        temperature: 0,
                        num_predict: 500
                    }
                })
            });

            const endTime = Date.now();
            const durationSeconds = (endTime - startTime) / 1000;
            console.log('PS567 AI call duration seconds:', durationSeconds);


            const data = await response.json();
            console.log('PS567 AI JSON full data:', JSON.stringify(data));  
            console.log('PS567 AI JSON full data:', data); 

            //gemini response
            //console.log('PS567 AI JSON content only:', data?.candidates?.[0]?.content);
            //openai console
            //console.log('PS567 AI JSON content only:', data?.choices?.[0]?.message?.content);
            //ollama console           
            console.log('PS0000567 AI JSON content only:', data?.message?.content);           
              

            if (data.error) {
                console.error('OpenAI Error:', data.error);
                throw new Error(data.error.message);
            }

            //gemini response
            //const aiResponseString = data?.candidates?.[0]?.content?.parts?.[0]?.text;
            // openai response
            //const aiResponseString = data.choices[0].message.content.replace(/```json/g, '').replace(/```/g, '').trim();
            // Ollama response 
            const aiResponseString = data.message.content.replace(/```json/g, '').replace(/```/g, '').trim();

            const generatedJson = JSON.parse(aiResponseString);

            // const endTime = Date.now();
            // const durationSeconds = (endTime - startTime) / 1000;
            //const durationSeconds = durationMs / 1000;
            //console.log('PS567 AI call duration seconds:', durationSeconds);


            // 5. Wrap the result in the format expected by the existing component logic
            return {
                DUMMY_OfferUIDefinition__c: JSON.stringify(generatedJson),
                DUMMY_Title__c: 'product Details'
            };


        } catch (error) {
            console.error('Error generating guided selling JSON from AI:', error);
            return null;
        }
    }

}