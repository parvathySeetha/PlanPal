console.log("WELCOME TO LIGHTNING STUDIO");

export const predefinedMessages  = {
  "Messages": [
    {
      "msgId": "msg1",
      "msgName": "unknownEvent",
      "msgDesc": "Unknown event passed. Please try again"
    },
    {
      "msgId": "msg2",
      "msgName": "contextMismatch",
      "msgDesc": "Context Object of the Item Selection Admin record doesn't match with the object type of  CartId"
    },
    {
      "msgId": "msg3",
      "msgName": "cartIdMissing",
      "msgDesc": "CartId is missing or invalid cart id "
    },
    {
      "msgId": "msg4",
      "msgName": "itemSelAdNameMissing",
      "msgDesc": "Item Selection Admin name is missing or invalid"
    },
    {
      "msgId": "msg5",
      "msgName": "cartIdError",
      "msgDesc": "A record of provided cartid doesn't exist in the org"
    }
  ]
};

export const inputJSON = {

"Events":[
  {
    "keyword": "ShowCatalog1",
    "event": "showCatalog",
    "payload": {
      "itemSelectionAdmin": "LineItemViewCatalog",
      "cartId": "0Q0dN0000026Hho0AG"
    }
  },
  {
    "keyword": "ShowCatalog2",
    "event": "showCatalog",
    "payload": {
      "itemSelectionAdmin": "LineItemViewCatalog",
      "cartId": "012dN000002c0Qx30AG"
    }
  },
  {
    "keyword": "ShowCatalog3",
    "event": "showCatalog",
    "payload": {
      "cartId": "0Q0dN0000026Hho0AG"
    }
  },
  {
    "keyword": "ShowCatalog4",
    "event": "showCatalog",
    "payload": {
      "itemSelectionAdmin": "LineItemViewCatalog"
    }
  },  



  {
    "keyword": "LineItemsView1",
    "event": "loadLineItemsView",
    "payload": {
      "cartId": "0Q0dN0000026Hho0AG"
    }
  },
  {
    "keyword": "LaptopGuidedSelling",
    "event": "launchGuidedSelling",
    "payload": {
      "cartId": "0Q0dN0000026HhlSAE",
      "priceBookEntryId": "01udN0000014Ei9QAE",
      "itemDetailFlowName": "GetProductDetails"
    }
  },
  {
    "keyword": "QuickbooksGuidedSelling",
    "event": "launchGuidedSelling",
    "payload": {
      "cartId": "0Q0dN0000026Hho0AG",
      "priceBookEntryId": "01udN0000014EiGYAI",
      "itemDetailFlowName": "GetProductDetails"
    }
  }  
]


};